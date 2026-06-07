"""Model-facing guidance for failed tool calls."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tiny_claw._internal.schema.message import ToolCall


@dataclass(frozen=True)
class ToolErrorTranslation:
    error_type: str
    summary: str
    next_action: str
    avoid: str
    retryable: bool
    suggested_tool: str | None = None

    def render(self, *, raw_error: str, attempt_count: int) -> str:
        lines = [
            f"工具失败：{self.summary}",
            "",
            "原始错误：",
            raw_error.strip() or "(empty error)",
            "",
            "下一步建议：",
            self.next_action,
            "",
            "不要做：",
            self.avoid,
            "",
            f"失败次数：{attempt_count}",
        ]
        if attempt_count >= 2:
            lines.extend(
                [
                    "",
                    "重复提醒：",
                    "这已经不是第一次失败；必须调整参数或换策略，不要继续原样重试。",
                ]
            )
        return "\n".join(lines)

    def metadata(self, *, attempt_count: int) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "error_type": self.error_type,
            "retryable": self.retryable,
            "attempt": attempt_count,
        }
        if self.suggested_tool is not None:
            metadata["suggested_tool"] = self.suggested_tool
        return metadata


@dataclass(frozen=True)
class ToolErrorTranslator:
    visible_tools: tuple[str, ...]

    def translate(
        self,
        *,
        tool_call: ToolCall,
        raw_error: str,
        attempt_count: int,
    ) -> ToolErrorTranslation:
        lower_error = raw_error.lower()
        if tool_call.name not in self.visible_tools:
            return self._unknown_tool(tool_call.name)
        if "unknown tool:" in lower_error:
            return self._unknown_tool(tool_call.name)
        if "must stay under the configured root" in lower_error:
            return ToolErrorTranslation(
                error_type="path_outside_root",
                summary="工具请求的路径超出了工作区边界。",
                next_action="换成工作区内的相对路径；如果目标确实在工作区外，请说明阻塞原因。",
                avoid="不要尝试绕过路径限制，也不要改用绝对路径重复调用。",
                retryable=True,
                suggested_tool=tool_call.name,
            )
        if tool_call.name == "read" and "path does not exist" in lower_error:
            return self._read_path_not_found(tool_call)
        if tool_call.name == "edit" and "could not find old_text" in lower_error:
            return self._edit_old_text_not_found()
        if tool_call.name == "edit" and "found multiple matches" in lower_error:
            return self._edit_multiple_matches()
        if tool_call.name == "bash" and "error=command timed out" in lower_error:
            return self._bash_timeout(tool_call)
        if tool_call.name == "bash" and "exit_code=" in lower_error:
            return self._bash_command_failed(raw_error)
        if "requires" in lower_error or "invalid" in lower_error:
            return ToolErrorTranslation(
                error_type="invalid_arguments",
                summary=f"{tool_call.name} 的参数不符合要求。",
                next_action="根据原始错误修正参数后再重试；只使用工具 schema 中声明的字段。",
                avoid="不要原样重复同一组参数，也不要编造未声明的字段。",
                retryable=True,
                suggested_tool=tool_call.name,
            )
        return ToolErrorTranslation(
            error_type="execution_error",
            summary=f"{tool_call.name} 返回了失败状态。",
            next_action="先阅读原始错误和输出内容，再换参数、换可见工具，或说明当前阻塞点。",
            avoid="不要声称工具已经成功，也不要用完全相同的参数反复重试。",
            retryable=True,
            suggested_tool=tool_call.name,
        )

    def repeat_call_blocked(
        self,
        *,
        tool_call: ToolCall,
        attempt_count: int,
    ) -> ToolErrorTranslation:
        return ToolErrorTranslation(
            error_type="repeat_call_blocked",
            summary=f"{tool_call.name} 用同一组参数连续失败，已阻止继续重复执行。",
            next_action="换一个策略：修改参数、调用其他可见工具收集新信息，或向用户说明阻塞原因。",
            avoid="不要继续提交完全相同的工具名和参数。",
            retryable=False,
        )

    def _unknown_tool(self, tool_name: str) -> ToolErrorTranslation:
        visible = ", ".join(self.visible_tools) if self.visible_tools else "none"
        return ToolErrorTranslation(
            error_type="unknown_tool",
            summary=f"模型请求了当前不可用的工具：{tool_name}。",
            next_action=f"只能使用当前可见工具：{visible}。如果没有合适工具，请说明阻塞原因。",
            avoid="不要编造工具名，也不要调用未暴露的工具。",
            retryable=bool(self.visible_tools),
        )

    def _read_path_not_found(self, tool_call: ToolCall) -> ToolErrorTranslation:
        path = str(tool_call.arguments.get("path", "")).strip()
        parent = _parent_dir(path)
        if "bash" in self.visible_tools:
            return ToolErrorTranslation(
                error_type="read_path_not_found",
                summary=f"read 找不到目标文件：{path or '(missing path)'}。",
                next_action=(
                    "先调用 bash 查看父目录是否存在以及文件名是否写错，"
                    f"例如：ls {shlex.quote(parent)}"
                ),
                avoid="不要用完全相同的 path 直接重复 read。",
                retryable=True,
                suggested_tool="bash",
            )
        return ToolErrorTranslation(
            error_type="read_path_not_found",
            summary=f"read 找不到目标文件：{path or '(missing path)'}。",
            next_action="当前没有可见的 bash 工具；请换一个已确认的相对路径，或向用户确认路径。",
            avoid="不要提示或调用当前不可用的 ls/bash，也不要原样重复 read。",
            retryable=True,
        )

    def _edit_old_text_not_found(self) -> ToolErrorTranslation:
        if "read" in self.visible_tools:
            return ToolErrorTranslation(
                error_type="edit_old_text_not_found",
                summary="edit 没找到要替换的 old_text。",
                next_action="先调用 read 读取目标文件的最新内容，确认当前文本后再重新 edit。",
                avoid="不要用完全相同的 old_text 直接重复 edit。",
                retryable=True,
                suggested_tool="read",
            )
        return ToolErrorTranslation(
            error_type="edit_old_text_not_found",
            summary="edit 没找到要替换的 old_text。",
            next_action=(
                "当前没有可见的 read 工具；请说明无法确认文件最新内容，或让用户提供准确片段。"
            ),
            avoid="不要用完全相同的 old_text 直接重复 edit。",
            retryable=True,
        )

    def _edit_multiple_matches(self) -> ToolErrorTranslation:
        if "read" in self.visible_tools:
            return ToolErrorTranslation(
                error_type="edit_multiple_matches",
                summary="edit 找到了多处匹配，无法确定该改哪一处。",
                next_action="先调用 read 查看相关区域，然后提供更长、更唯一的 old_text 再 edit。",
                avoid="不要用过短或会匹配多处的 old_text 反复 edit。",
                retryable=True,
                suggested_tool="read",
            )
        return ToolErrorTranslation(
            error_type="edit_multiple_matches",
            summary="edit 找到了多处匹配，无法确定该改哪一处。",
            next_action="提供更长、更唯一的 old_text；如果无法确认上下文，请向用户说明阻塞。",
            avoid="不要用过短或会匹配多处的 old_text 反复 edit。",
            retryable=True,
        )

    def _bash_timeout(self, tool_call: ToolCall) -> ToolErrorTranslation:
        command = str(tool_call.arguments.get("command", ""))
        if _looks_like_service_command(command):
            return ToolErrorTranslation(
                error_type="bash_timeout_service",
                summary="bash 命令超时，且它看起来像长期运行的服务或 watch 进程。",
                next_action="如果确实需要启动服务，请改成后台运行并把输出重定向到日志文件，再用短命令检查日志。",
                avoid="不要把长期运行服务当作一次性命令反复前台执行。",
                retryable=True,
                suggested_tool="bash",
            )
        if _looks_like_test_or_build_command(command):
            return ToolErrorTranslation(
                error_type="bash_timeout",
                summary="bash 测试或构建命令超时。",
                next_action="缩小执行范围、使用更短的命令，或读取已有日志定位卡住的位置。",
                avoid="不要直接后台运行测试或构建来掩盖卡住的问题。",
                retryable=True,
                suggested_tool="bash",
            )
        return ToolErrorTranslation(
            error_type="bash_timeout",
            summary="bash 命令超时。",
            next_action=(
                "先缩小命令范围或增加诊断输出；只有确认它是服务/watch 命令时才考虑后台运行。"
            ),
            avoid="不要在不确定命令性质时直接后台运行。",
            retryable=True,
            suggested_tool="bash",
        )

    def _bash_command_failed(self, raw_error: str) -> ToolErrorTranslation:
        if "stdout_truncated=true" in raw_error or "stderr_truncated=true" in raw_error:
            next_action = "先阅读 stdout/stderr；如果输出被截断，请缩小命令范围或读取日志文件。"
        else:
            next_action = "先阅读 stdout/stderr，根据具体报错修正命令或输入后再重试。"
        return ToolErrorTranslation(
            error_type="bash_command_failed",
            summary="bash 命令非零退出。",
            next_action=next_action,
            avoid="不要忽略非零退出码，也不要在没有修正命令的情况下原样重试。",
            retryable=True,
            suggested_tool="bash",
        )


def _parent_dir(path: str) -> str:
    normalized = path.strip().rstrip("/")
    if not normalized or "/" not in normalized:
        return "."
    parent = normalized.rsplit("/", maxsplit=1)[0]
    return parent or "."


def _looks_like_service_command(command: str) -> bool:
    normalized = f" {command.lower()} "
    service_markers = (
        " serve ",
        " runserver ",
        " uvicorn ",
        " gunicorn ",
        " flask run ",
        " npm run dev ",
        " pnpm dev ",
        " yarn dev ",
        " next dev ",
        " vite ",
        " nodemon ",
        " python -m http.server ",
        " tail -f ",
        " watch ",
    )
    return any(marker in normalized for marker in service_markers)


def _looks_like_test_or_build_command(command: str) -> bool:
    normalized = f" {command.lower()} "
    test_or_build_markers = (
        " pytest",
        " ruff",
        " mypy",
        " test ",
        " tests/",
        " build ",
        " compile ",
        " npm test",
        " pnpm test",
        " yarn test",
    )
    return any(marker in normalized for marker in test_or_build_markers)


def tool_call_key(tool_call: ToolCall) -> str:
    return f"{tool_call.name}:{_stable_arguments(tool_call.arguments)}"


def _stable_arguments(arguments: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(arguments), sort_keys=True, ensure_ascii=True, default=str)
