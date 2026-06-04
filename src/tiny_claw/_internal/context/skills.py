"""Project skill discovery and selection."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_SKILL_FILE_CHARS = 16_000
SKILL_DIR = ".claw/skills"
SKILL_FILE = "SKILL.md"
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_EXPLICIT_INVOCATION = re.compile(r"^\s*[$/]([a-z][a-z0-9-]*)(?:\s+(.*))?\s*$", re.DOTALL)
_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,}")
_STOPWORDS = {
    "and",
    "for",
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "please",
    "need",
    "want",
    "use",
    "using",
}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when_to_use: str
    argument_hint: str
    allowed_tools: tuple[str, ...]
    disable_model_invocation: bool
    user_invocable: bool
    paths: tuple[str, ...]
    body: str
    source_path: Path
    triggers: tuple[str, ...] = ()
    truncated: bool = False

    @property
    def directory(self) -> Path:
        return self.source_path.parent


@dataclass(frozen=True)
class SkillSelection:
    skill: Skill | None
    arguments: str = ""
    explicit: bool = False

    @property
    def selected_skills(self) -> tuple[Skill, ...]:
        if self.skill is None:
            return ()
        return (self.skill,)


@dataclass(frozen=True)
class SkillRegistry:
    workdir: Path
    max_skill_file_chars: int = DEFAULT_MAX_SKILL_FILE_CHARS

    @property
    def root(self) -> Path:
        return self.workdir / SKILL_DIR

    def discover(self) -> tuple[Skill, ...]:
        if not self.root.is_dir():
            return ()

        skills: list[Skill] = []
        seen: set[str] = set()
        for skill_dir in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not skill_dir.is_dir():
                continue
            skill = _read_skill(
                skill_dir / SKILL_FILE,
                fallback_name=skill_dir.name,
                max_chars=self.max_skill_file_chars,
            )
            if skill is None:
                continue
            if skill.name in seen:
                continue
            seen.add(skill.name)
            skills.append(skill)
        return tuple(skills)


@dataclass(frozen=True)
class SkillSelector:
    min_auto_score: int = 2

    def select(self, prompt: str, skills: Sequence[Skill]) -> SkillSelection:
        explicit = _explicit_invocation(prompt)
        if explicit is not None:
            requested_name, arguments = explicit
            skill_by_name = {skill.name: skill for skill in skills if skill.user_invocable}
            skill = skill_by_name.get(requested_name)
            if skill is not None:
                return SkillSelection(skill=skill, arguments=arguments, explicit=True)
            return SkillSelection(skill=None)

        scored = [
            (self._score(prompt, skill), skill)
            for skill in skills
            if not skill.disable_model_invocation
        ]
        scored = [(score, skill) for score, skill in scored if score >= self.min_auto_score]
        if not scored:
            return SkillSelection(skill=None)
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return SkillSelection(skill=scored[0][1], arguments=prompt)

    def _score(self, prompt: str, skill: Skill) -> int:
        prompt_text = prompt.lower()
        score = 0

        if skill.name in prompt_text:
            score += 5

        for trigger in skill.triggers:
            normalized_trigger = trigger.lower().strip()
            if normalized_trigger and normalized_trigger in prompt_text:
                score += 4

        prompt_tokens = _tokens(prompt)
        metadata_tokens = _tokens(
            " ".join(
                (
                    skill.name.replace("-", " "),
                    skill.description,
                    skill.when_to_use,
                    " ".join(skill.triggers),
                )
            )
        )
        return score + len(prompt_tokens & metadata_tokens)


def _read_skill(path: Path, *, fallback_name: str, max_chars: int) -> Skill | None:
    if not path.is_file():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    truncated = len(raw) > max_chars
    if len(raw) > max_chars:
        raw = raw[:max_chars]

    metadata, body = _split_frontmatter(raw)
    metadata_name = _string_metadata(metadata, "name")
    name = metadata_name if _is_skill_name(metadata_name) else fallback_name
    if not _is_skill_name(name):
        return None

    return Skill(
        name=name,
        description=_string_metadata(metadata, "description"),
        when_to_use=_string_metadata(metadata, "when_to_use", "when-to-use"),
        argument_hint=_string_metadata(metadata, "argument_hint", "argument-hint"),
        allowed_tools=_list_metadata(metadata, "allowed_tools", "allowed-tools"),
        disable_model_invocation=_bool_metadata(
            metadata,
            "disable_model_invocation",
            "disable-model-invocation",
        ),
        user_invocable=_bool_metadata(metadata, "user_invocable", "user-invocable", default=True),
        paths=_list_metadata(metadata, "paths"),
        body=body.strip(),
        source_path=path,
        triggers=_list_metadata(metadata, "triggers"),
        truncated=truncated,
    )


def _split_frontmatter(raw: str) -> tuple[Mapping[str, object], str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, raw

    metadata = _parse_frontmatter(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    return metadata, body


def _parse_frontmatter(lines: Sequence[str]) -> Mapping[str, object]:
    metadata: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if current_key is not None and stripped.startswith("- "):
            assert current_list is not None
            current_list.append(_strip_quotes(stripped[2:].strip()))
            continue

        current_key = None
        current_list = None
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        normalized_key = key.strip().lower()
        stripped_value = value.strip()
        if not normalized_key:
            continue
        if not stripped_value:
            current_key = normalized_key
            current_list = []
            metadata[normalized_key] = current_list
            continue
        metadata[normalized_key] = _parse_scalar_or_list(stripped_value)

    return metadata


def _parse_scalar_or_list(value: str) -> object:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return tuple(_strip_quotes(item.strip()) for item in inner.split(",") if item.strip())
    return _strip_quotes(value)


def _string_metadata(metadata: Mapping[str, object], *names: str) -> str:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, str):
            return value.strip()
    return ""


def _list_metadata(metadata: Mapping[str, object], *names: str) -> tuple[str, ...]:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, Sequence) and not isinstance(value, str):
            return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _bool_metadata(metadata: Mapping[str, object], *names: str, default: bool = False) -> bool:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1", "on"}:
                return True
            if normalized in {"false", "no", "0", "off"}:
                return False
    return default


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _explicit_invocation(prompt: str) -> tuple[str, str] | None:
    match = _EXPLICIT_INVOCATION.match(prompt)
    if match is None:
        return None
    return match.group(1), (match.group(2) or "").strip()


def _is_skill_name(value: str) -> bool:
    return bool(SKILL_NAME_PATTERN.fullmatch(value))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }
