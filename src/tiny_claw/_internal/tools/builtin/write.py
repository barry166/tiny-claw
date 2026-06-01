"""UTF-8 file write tool bounded by the configured root."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin._common import resolve_under_root

WriteMode = Literal["overwrite", "append"]
WRITE_MODES: set[str] = {"overwrite", "append"}


@dataclass(frozen=True)
class WriteTool:
    root: Path

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Write or append UTF-8 text to a file under the configured root."

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
                    "description": "UTF-8 text content to write.",
                },
                "mode": {
                    "type": "string",
                    "enum": sorted(WRITE_MODES),
                    "default": "overwrite",
                    "description": (
                        "Use overwrite to replace the file, or append to append content."
                    ),
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
        payload = input.arguments
        relative_path = str(payload.get("path", "")).strip()
        content = str(payload.get("content", ""))
        mode = str(payload.get("mode", "overwrite")).strip()
        if mode not in WRITE_MODES:
            raise ToolError("write tool requires 'mode' to be one of: append, overwrite")

        target = resolve_under_root(self.root, relative_path, tool_name=self.name)
        if target.exists() and target.is_dir():
            raise ToolError(f"write path is a directory: {relative_path}")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                target.write_text(
                    _existing_text(target) + content,
                    encoding="utf-8",
                )
            else:
                _atomic_write_text(target, content)
        except OSError as exc:
            raise ToolError(f"write tool failed for {relative_path}: {exc}") from exc

        byte_count = len(content.encode("utf-8"))
        line_count = len(content.splitlines())
        return ToolOutput(
            content=(f"path={relative_path}\nmode={mode}\nbytes={byte_count}\nlines={line_count}")
        )


def _existing_text(target: Path) -> str:
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("write append only supports existing UTF-8 text files") from exc


def _atomic_write_text(target: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
