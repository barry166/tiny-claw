"""Read-only file tool bounded by the configured root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput

DEFAULT_MAX_LINES = 200


@dataclass(frozen=True)
class ReadTool:
    root: Path
    default_max_lines: int = DEFAULT_MAX_LINES

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read a UTF-8 text file under the configured root with optional line range."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under the configured root.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based line number to start reading from.",
                    "default": 1,
                    "minimum": 1,
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to return.",
                    "default": self.default_max_lines,
                    "minimum": 1,
                },
            },
            "required": ["path"],
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
        if not relative_path:
            raise ToolError("read tool requires a non-empty 'path' field")

        start_line = _positive_int(payload.get("start_line", 1), field_name="start_line")
        max_lines = _positive_int(
            payload.get("max_lines", self.default_max_lines),
            field_name="max_lines",
        )

        root = self.root.resolve()
        target = (root / relative_path).resolve()
        if not target.is_relative_to(root):
            raise ToolError("read path must stay under the configured root")
        if not target.exists():
            raise ToolError(f"read path does not exist: {relative_path}")
        if target.is_dir():
            raise ToolError(f"read path is a directory: {relative_path}")

        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ToolError("read tool only supports UTF-8 text files") from exc

        start_index = start_line - 1
        selected = lines[start_index : start_index + max_lines]
        if not selected:
            return ToolOutput(content=f"no content: start_line {start_line} is beyond end of file")

        numbered_lines = (
            f"{line_number}: {line}" for line_number, line in enumerate(selected, start=start_line)
        )
        return ToolOutput(content="\n".join(numbered_lines))


def _positive_int(value: object, *, field_name: str) -> int:
    error_message = f"read tool requires '{field_name}' to be a positive integer"
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
