"""Disabled-by-default bash tool skeleton."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.tools.registry import ToolResult


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

    def run(self, payload: Mapping[str, object]) -> ToolResult:
        if not self.enabled:
            raise ToolError("bash tool is disabled by default")

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
        return ToolResult(output=output.strip())
