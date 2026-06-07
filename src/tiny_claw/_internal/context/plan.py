"""Session-scoped plan files and prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

_PLAN_WRITE_LOCK = Lock()
_TODO_PATTERN = re.compile(r"^\s*-\s*\[(?P<done>[ xX])\]\s+(?P<body>.+?)\s*$")
_TODO_ID_PATTERN = re.compile(r"^(?P<id>[A-Z][A-Z0-9]*-\d{3,})\s+(?P<text>.+)$")


@dataclass(frozen=True)
class TodoItem:
    id: str
    text: str
    done: bool
    line_index: int


@dataclass(frozen=True)
class PlanSnapshot:
    plan_text: str
    todo_text: str
    next_todo: TodoItem | None


@dataclass(frozen=True)
class PlanFiles:
    root: Path

    @classmethod
    def from_session_root(cls, session_root: Path) -> PlanFiles:
        return cls(root=session_root / "plan")

    @property
    def plan_path(self) -> Path:
        return self.root / "PLAN.md"

    @property
    def todo_path(self) -> Path:
        return self.root / "TODO.md"

    @property
    def exists(self) -> bool:
        return self.plan_path.exists() and self.todo_path.exists()

    def read_snapshot(self) -> PlanSnapshot:
        plan_text = _read_text(self.plan_path)
        todo_text = _read_text(self.todo_path)
        return PlanSnapshot(
            plan_text=plan_text,
            todo_text=todo_text,
            next_todo=PlanMarkdownParser.next_open_item(todo_text),
        )

    def create_missing(self, *, plan_text: str, todo_text: str) -> PlanSnapshot:
        self.root.mkdir(parents=True, exist_ok=True)
        with _PLAN_WRITE_LOCK:
            if not self.plan_path.exists():
                _atomic_write(self.plan_path, _ensure_trailing_newline(plan_text))
            if not self.todo_path.exists():
                _atomic_write(self.todo_path, _ensure_trailing_newline(todo_text))
        return self.read_snapshot()

    def mark_done(self, todo_id: str) -> PlanSnapshot:
        with _PLAN_WRITE_LOCK:
            updated = PlanMarkdownParser.mark_done(_read_text(self.todo_path), todo_id)
            _atomic_write(self.todo_path, _ensure_trailing_newline(updated))
        return self.read_snapshot()

    def append_blocker(self, *, todo: TodoItem, reason: str) -> PlanSnapshot:
        with _PLAN_WRITE_LOCK:
            todo_text = _read_text(self.todo_path)
            updated = PlanMarkdownParser.append_blocker(todo_text, todo=todo, reason=reason)
            _atomic_write(self.todo_path, _ensure_trailing_newline(updated))
        return self.read_snapshot()


class PlanMarkdownParser:
    @staticmethod
    def items(todo_text: str) -> tuple[TodoItem, ...]:
        parsed: list[TodoItem] = []
        for index, line in enumerate(todo_text.splitlines()):
            match = _TODO_PATTERN.match(line)
            if match is None:
                continue

            body = match.group("body").strip()
            id_match = _TODO_ID_PATTERN.match(body)
            if id_match is None:
                todo_id = f"TC-{len(parsed) + 1:03d}"
                text = body
            else:
                todo_id = id_match.group("id")
                text = id_match.group("text")
            parsed.append(
                TodoItem(
                    id=todo_id,
                    text=text.strip(),
                    done=match.group("done").lower() == "x",
                    line_index=index,
                )
            )
        return tuple(parsed)

    @staticmethod
    def next_open_item(todo_text: str) -> TodoItem | None:
        for item in PlanMarkdownParser.items(todo_text):
            if not item.done:
                return item
        return None

    @staticmethod
    def mark_done(todo_text: str, todo_id: str) -> str:
        lines = todo_text.splitlines()
        for item in PlanMarkdownParser.items(todo_text):
            if item.id != todo_id:
                continue
            line = lines[item.line_index]
            lines[item.line_index] = re.sub(r"\[[ xX]\]", "[x]", line, count=1)
            return "\n".join(lines)
        raise ValueError(f"TODO item not found: {todo_id}")

    @staticmethod
    def append_blocker(todo_text: str, *, todo: TodoItem, reason: str) -> str:
        cleaned_reason = " ".join(reason.strip().split()) or "No details provided."
        lines = todo_text.splitlines()
        if "## Blockers" not in lines:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["## Blockers", ""])
        lines.append(f"- {todo.id}: {cleaned_reason}")
        return "\n".join(lines)


class PlanPromptBuilder:
    @staticmethod
    def create_system_prompt() -> str:
        return (
            "Plan Mode is ON. Create a persistent task plan for the user request. "
            "Do not call tools. Return exactly two Markdown documents wrapped in "
            "<PLAN_MD>...</PLAN_MD> and <TODO_MD>...</TODO_MD>. PLAN.md must include "
            "sections for goal understanding, architecture design, technical choices, "
            "global planning, constraints, risks, and verification strategy. TODO.md "
            "must use stable checkbox IDs like '- [ ] TC-001 Create project files'."
        )

    @staticmethod
    def resume_system_prompt(snapshot: PlanSnapshot) -> str:
        return (
            "Plan Mode is ON. Existing session plan files were found. Do not overwrite "
            "them and do not call tools. Summarize the current plan state and identify "
            f"the next unfinished TODO item: {_format_next_todo(snapshot.next_todo)}."
        )

    @staticmethod
    def execute_current_task_prompt(snapshot: PlanSnapshot) -> str:
        return (
            "Plan Mode is ON. Continue from the persisted session plan files below. "
            "Execute exactly the current TODO item, and do not work ahead.\n\n"
            f"Current TODO: {_format_next_todo(snapshot.next_todo)}\n\n"
            "When the current TODO is complete, include the exact line "
            "'PLAN_STEP_STATUS: completed' in your final response. If blocked, include "
            "'PLAN_STEP_STATUS: blocked' and explain why.\n\n"
            "PLAN.md:\n"
            f"{snapshot.plan_text.strip()}\n\n"
            "TODO.md:\n"
            f"{snapshot.todo_text.strip()}"
        )

    @staticmethod
    def render_plan_result(snapshot: PlanSnapshot) -> str:
        return (
            f"{snapshot.plan_text.strip()}\n\n"
            f"{snapshot.todo_text.strip()}\n\n"
            f"Next TODO: {_format_next_todo(snapshot.next_todo)}"
        ).strip()

    @staticmethod
    def render_all_done(snapshot: PlanSnapshot) -> str:
        return (
            f"{snapshot.plan_text.strip()}\n\n"
            f"{snapshot.todo_text.strip()}\n\n"
            "All TODO items are complete."
        ).strip()

    @staticmethod
    def render_plan_memory(snapshot: PlanSnapshot, *, plan_files: PlanFiles) -> str:
        return f"session_plan={plan_files.root}\nnext_todo={_format_next_todo(snapshot.next_todo)}"


class PlanResponseParser:
    @staticmethod
    def parse_create_response(*, response_text: str, prompt: str) -> tuple[str, str]:
        plan_text = _extract_tag(response_text, "PLAN_MD")
        todo_text = _extract_tag(response_text, "TODO_MD")
        if plan_text is None:
            plan_text = _default_plan_text(prompt=prompt, response_text=response_text)
        if todo_text is None:
            todo_text = _default_todo_text(prompt=prompt)
        return _normalize_plan_text(plan_text), _normalize_todo_text(todo_text)


def plan_step_status(text: str) -> str | None:
    match = re.search(r"PLAN_STEP_STATUS:\s*(completed|blocked|needs_replan)", text, re.I)
    if match is None:
        return None
    return match.group(1).lower()


def _extract_tag(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>\s*(?P<body>.*?)\s*</{tag}>", text, re.S | re.I)
    if match is None:
        return None
    return match.group("body").strip()


def _normalize_plan_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("#"):
        return cleaned
    return f"# PLAN.md\n\n{cleaned}"


def _normalize_todo_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return _default_todo_text(prompt="")
    lines = []
    item_count = 0
    for line in cleaned.splitlines():
        match = _TODO_PATTERN.match(line)
        if match is None:
            lines.append(line)
            continue
        body = match.group("body").strip()
        if _TODO_ID_PATTERN.match(body) is None:
            item_count += 1
            lines.append(f"- [{match.group('done')}] TC-{item_count:03d} {body}")
        else:
            item_count += 1
            lines.append(line)
    normalized = "\n".join(lines).strip()
    return normalized if normalized.startswith("#") else f"# TODO.md\n\n{normalized}"


def _default_plan_text(*, prompt: str, response_text: str) -> str:
    source = response_text.strip() or prompt.strip() or "No prompt provided."
    return (
        "# PLAN.md\n\n"
        "## 目标理解\n\n"
        f"{source}\n\n"
        "## 架构设计\n\n"
        "待根据任务细化。\n\n"
        "## 技术选型\n\n"
        "沿用当前项目技术栈。\n\n"
        "## 全局规划\n\n"
        "先完成当前请求的最小可验证实现，再运行验证命令。\n\n"
        "## 约束条件\n\n"
        "保持变更小而可回滚，不绕过工具权限。\n\n"
        "## 风险与验证策略\n\n"
        "通过聚焦测试和 CLI 冒烟验证确认行为。"
    )


def _default_todo_text(*, prompt: str) -> str:
    task = prompt.strip() or "完成当前用户请求"
    return f"# TODO.md\n\n- [ ] TC-001 {task}"


def _format_next_todo(todo: TodoItem | None) -> str:
    if todo is None:
        return "none"
    return f"{todo.id} {todo.text}"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"
