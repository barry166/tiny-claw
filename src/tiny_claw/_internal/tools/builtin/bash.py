"""Disabled-by-default bash tool skeleton."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput


@dataclass(frozen=True)
class BashTool:
    enabled: bool = False
    timeout_seconds: int = 30

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Run a shell command when explicitly enabled."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                }
            },
            "required": ["command"],
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
            raise ToolError("bash tool is disabled by default")

        payload = input.arguments
        command = str(payload.get("command", "")).strip()
        if not command:
            raise ToolError("bash tool requires a non-empty 'command' field")

        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        output = completed.stdout if completed.returncode == 0 else completed.stderr
        return ToolOutput(content=output.strip(), is_error=completed.returncode != 0)
