"""Disabled-by-default file edit tool skeleton."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.tools.registry import ToolResult


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

    def run(self, payload: Mapping[str, object]) -> ToolResult:
        if not self.enabled:
            raise ToolError("edit tool is disabled by default")

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
        return ToolResult(output=f"wrote {target}")
