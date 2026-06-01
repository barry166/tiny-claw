"""UTF-8 local string replacement tool bounded by the configured root."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin._common import resolve_under_root

SNIPPET_CONTEXT_LINES = 2


class MatchSpan(NamedTuple):
    start: int
    end: int


class MatchResult(NamedTuple):
    strategy: str
    search_text: str
    spans: tuple[MatchSpan, ...]
    indent: str = ""


class IndexedLine(NamedTuple):
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class EditTool:
    root: Path

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "对现有文件进行局部的字符串替换。这比重写整个文件更安全、更快速。"
            "请提供足够的 old_text 上下文以确保匹配的唯一性。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under the configured root.",
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "Existing text to replace. Include enough context to match exactly once."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text. Use an empty string to delete old_text.",
                },
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        payload = input.arguments
        relative_path = str(payload.get("path", "")).strip()
        old_text = str(payload.get("old_text", ""))
        new_text = str(payload.get("new_text", ""))

        if not old_text:
            raise ToolError("edit tool requires a non-empty 'old_text' field")
        if old_text == new_text:
            raise ToolError("edit tool requires 'old_text' and 'new_text' to be different")

        target = resolve_under_root(self.root, relative_path, tool_name=self.name)
        if not target.exists():
            raise ToolError(f"edit path does not exist: {relative_path}")
        if target.is_dir():
            raise ToolError(f"edit path is a directory: {relative_path}")

        try:
            with target.open("r", encoding="utf-8", newline="") as handle:
                original = handle.read()
        except UnicodeDecodeError as exc:
            raise ToolError("edit tool only supports UTF-8 text files") from exc
        except OSError as exc:
            raise ToolError(f"edit tool failed to read {relative_path}: {exc}") from exc

        match = _find_unique_match(original, old_text)
        if match is None:
            hint = _line_number_hint(old_text)
            raise ToolError(f"edit tool could not find old_text in {relative_path}{hint}")
        if len(match.spans) > 1:
            line_numbers = _line_numbers_for_spans(original, match.spans)
            raise ToolError(
                "edit tool found multiple matches for old_text in "
                f"{relative_path} at lines {line_numbers}; please provide more context"
            )

        span = match.spans[0]
        replaced_text = original[span.start : span.end]
        replacement_text = _prepare_replacement_text(
            new_text,
            replaced_text=replaced_text,
            match=match,
        )
        updated = original[: span.start] + replacement_text + original[span.end :]
        try:
            _atomic_write_text(target, updated)
        except OSError as exc:
            raise ToolError(f"edit tool failed to write {relative_path}: {exc}") from exc

        start_line = _line_number_at(original, span.start)
        old_bytes = len(replaced_text.encode("utf-8"))
        new_bytes = len(replacement_text.encode("utf-8"))
        snippet = _render_snippet(updated, start_line=start_line, replacement_text=replacement_text)
        return ToolOutput(
            content="\n".join(
                [
                    f"path={relative_path}",
                    f"strategy={match.strategy}",
                    "replacements=1",
                    f"start_line={start_line}",
                    f"old_bytes={old_bytes}",
                    f"new_bytes={new_bytes}",
                    "snippet:",
                    snippet,
                ]
            )
        )


def _find_unique_match(content: str, old_text: str) -> MatchResult | None:
    for candidate in (
        _exact_match(content, old_text),
        _newline_normalized_match(content, old_text),
        _trim_space_match(content, old_text),
        _line_by_line_normalized_match(content, old_text),
    ):
        if candidate.spans:
            return candidate
    return None


def _exact_match(content: str, old_text: str) -> MatchResult:
    return MatchResult(
        strategy="exact",
        search_text=old_text,
        spans=_literal_spans(content, old_text),
    )


def _newline_normalized_match(content: str, old_text: str) -> MatchResult:
    normalized_content, offset_map = _normalize_newlines_with_offsets(content)
    normalized_old_text = _normalize_newlines(old_text)
    normalized_spans = _literal_spans(normalized_content, normalized_old_text)
    spans = tuple(
        MatchSpan(
            start=offset_map[span.start],
            end=offset_map[span.end],
        )
        for span in normalized_spans
    )
    return MatchResult(
        strategy="newline_normalized",
        search_text=normalized_old_text,
        spans=spans,
    )


def _trim_space_match(content: str, old_text: str) -> MatchResult:
    trimmed = old_text.strip()
    if not trimmed or trimmed == old_text:
        return MatchResult(strategy="trim_space", search_text=trimmed, spans=())
    return MatchResult(
        strategy="trim_space",
        search_text=trimmed,
        spans=_literal_spans(content, trimmed),
    )


def _line_by_line_normalized_match(content: str, old_text: str) -> MatchResult:
    old_lines = _strip_common_indent_lines(old_text.strip("\r\n"))
    if not old_lines:
        return MatchResult(strategy="line_by_line_normalized", search_text="", spans=())

    content_lines = _indexed_lines(content)
    old_line_count = len(old_lines)
    old_text_has_trailing_newline = old_text.endswith(("\n", "\r"))
    spans: list[MatchSpan] = []
    matched_indent = ""

    for index in range(0, len(content_lines) - old_line_count + 1):
        chunk = content_lines[index : index + old_line_count]
        chunk_text = "".join(line.text for line in chunk).strip("\r\n")
        if _strip_common_indent_lines(chunk_text) == old_lines:
            chunk_indent = _common_literal_indent(*(line.text for line in chunk))
            end = chunk[-1].end
            if not old_text_has_trailing_newline:
                end = _line_content_end(chunk[-1])
            if not spans:
                matched_indent = chunk_indent
            spans.append(MatchSpan(start=chunk[0].start, end=end))

    return MatchResult(
        strategy="line_by_line_normalized",
        search_text="\n".join(old_lines),
        spans=tuple(spans),
        indent=matched_indent if len(spans) == 1 else "",
    )


def _literal_spans(content: str, needle: str) -> tuple[MatchSpan, ...]:
    if not needle:
        return ()

    spans: list[MatchSpan] = []
    start = 0
    while True:
        index = content.find(needle, start)
        if index < 0:
            break
        spans.append(MatchSpan(start=index, end=index + len(needle)))
        start = index + 1
    return tuple(spans)


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_newlines_with_offsets(value: str) -> tuple[str, tuple[int, ...]]:
    chars: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(value):
        offsets.append(index)
        char = value[index]
        if char == "\r":
            chars.append("\n")
            if index + 1 < len(value) and value[index + 1] == "\n":
                index += 2
            else:
                index += 1
            continue
        chars.append(char)
        index += 1
    offsets.append(len(value))
    return "".join(chars), tuple(offsets)


def _indexed_lines(content: str) -> tuple[IndexedLine, ...]:
    lines: list[IndexedLine] = []
    start = 0
    for line in content.splitlines(keepends=True):
        end = start + len(line)
        lines.append(IndexedLine(text=line, start=start, end=end))
        start = end
    return tuple(lines)


def _line_content_end(line: IndexedLine) -> int:
    text = line.text
    if text.endswith("\r\n"):
        return line.end - 2
    if text.endswith(("\n", "\r")):
        return line.end - 1
    return line.end


def _strip_common_indent_lines(value: str) -> tuple[str, ...]:
    lines = _normalize_newlines(value).split("\n")
    common = _common_literal_indent(*lines)
    if not common:
        return tuple(line.rstrip("\r\n") for line in lines)
    return tuple(line.removeprefix(common).rstrip("\r\n") for line in lines)


def _common_literal_indent(*lines: str) -> str:
    prefixes = [_leading_whitespace(line) for line in lines if line.strip()]
    if not prefixes:
        return ""

    common = prefixes[0]
    for prefix in prefixes[1:]:
        while common and not prefix.startswith(common):
            common = common[:-1]
    return common


def _leading_whitespace(line: str) -> str:
    index = 0
    while index < len(line) and line[index] in {" ", "\t"}:
        index += 1
    return line[:index]


def _line_numbers_for_spans(content: str, spans: tuple[MatchSpan, ...]) -> list[int]:
    return [_line_number_at(content, span.start) for span in spans]


def _line_number_at(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _prepare_replacement_text(new_text: str, *, replaced_text: str, match: MatchResult) -> str:
    replacement_text = _adapt_replacement_newlines(new_text, replaced_text)
    if match.strategy != "line_by_line_normalized" or not match.indent:
        return replacement_text
    return _apply_indent_if_unindented(replacement_text, match.indent)


def _adapt_replacement_newlines(new_text: str, replaced_text: str) -> str:
    if "\r\n" in replaced_text:
        return _normalize_newlines(new_text).replace("\n", "\r\n")
    if "\r" in replaced_text:
        return _normalize_newlines(new_text).replace("\n", "\r")
    return new_text


def _apply_indent_if_unindented(text: str, indent: str) -> str:
    line_separator = _line_separator_for(text)
    lines = _normalize_newlines(text).split("\n")
    nonblank_lines = [line for line in lines if line.strip()]
    if not nonblank_lines:
        return text
    if any(_leading_whitespace(line) for line in nonblank_lines):
        return text
    return line_separator.join(indent + line if line.strip() else line for line in lines)


def _line_separator_for(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _line_number_hint(old_text: str) -> str:
    lines = old_text.splitlines()
    numbered = [line for line in lines if line.lstrip().split(":", 1)[0].isdigit()]
    if numbered:
        return "; remove read-tool line number prefixes from old_text and try again"
    return ""


def _render_snippet(content: str, *, start_line: int, replacement_text: str) -> str:
    replacement_lines = max(1, len(replacement_text.splitlines()))
    lines = content.splitlines()
    first_line = max(1, start_line - SNIPPET_CONTEXT_LINES)
    last_line = min(len(lines), start_line + replacement_lines + SNIPPET_CONTEXT_LINES - 1)
    if not lines:
        return ""
    return "\n".join(
        f"{line_number}: {lines[line_number - 1]}"
        for line_number in range(first_line, last_line + 1)
    )


def _atomic_write_text(target: Path, content: str) -> None:
    stat_result = target.stat()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        shutil.copystat(target, temp_path)
        os.chmod(temp_path, stat_result.st_mode)
        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
