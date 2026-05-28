"""Disabled-by-default file edit tool skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput


@dataclass(frozen=True)
class EditTool:
    root: Path
    enabled: bool = False

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "Write text to a path under the configured root when explicitly enabled."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under the configured root.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        if not self.enabled:
            raise ToolError("edit tool is disabled by default")

        payload = input.arguments
        relative_path = str(payload.get("path", "")).strip()
        content = str(payload.get("content", ""))
        if not relative_path:
            raise ToolError("edit tool requires a non-empty 'path' field")

        root = self.root.resolve()
        target = (root / relative_path).resolve()
        if not target.is_relative_to(root):
            raise ToolError("edit path must stay under the configured root")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolOutput(content=f"wrote {target}")
