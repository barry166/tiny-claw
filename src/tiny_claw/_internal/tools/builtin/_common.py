"""Shared helpers for built-in tools."""

from __future__ import annotations

from pathlib import Path

from tiny_claw._internal.errors import ToolError


def resolve_under_root(root: Path, relative_path: str, *, tool_name: str) -> Path:
    path = relative_path.strip()
    if not path:
        raise ToolError(f"{tool_name} tool requires a non-empty 'path' field")
    if Path(path).is_absolute():
        raise ToolError(f"{tool_name} tool requires a relative path")

    resolved_root = root.resolve()
    target = (resolved_root / path).resolve()
    if not target.is_relative_to(resolved_root):
        raise ToolError(f"{tool_name} path must stay under the configured root")
    return target


def positive_int(value: object, *, field_name: str, tool_name: str) -> int:
    error_message = f"{tool_name} tool requires '{field_name}' to be a positive integer"
    if isinstance(value, bool):
        raise ToolError(error_message)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ToolError(error_message) from exc
    else:
        raise ToolError(error_message)
    if parsed < 1:
        raise ToolError(error_message)
    return parsed


def truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    marker = f"\n...<truncated {len(text) - max_chars} chars>...\n"
    available = max_chars - len(marker)
    if available <= 0:
        return marker.strip(), True

    head_chars = available // 2
    tail_chars = available - head_chars
    return text[:head_chars] + marker + text[-tail_chars:], True
