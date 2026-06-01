"""Bash command tool bounded by the configured workdir."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin._common import (
    positive_int,
    resolve_under_root,
    truncate_text,
)

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_OUTPUT_CHARS = 12000


@dataclass(frozen=True)
class BashTool:
    workdir: Path
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Run a shell command under the configured workdir."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "cwd": {
                    "type": "string",
                    "default": ".",
                    "description": "Relative working directory under the configured workdir.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": self.timeout_seconds,
                    "minimum": 1,
                    "maximum": self.timeout_seconds,
                    "description": "Command timeout in seconds.",
                },
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
        payload = input.arguments
        command = str(payload.get("command", "")).strip()
        if not command:
            raise ToolError("bash tool requires a non-empty 'command' field")

        cwd = str(payload.get("cwd", ".")).strip() or "."
        resolved_cwd = resolve_under_root(self.workdir, cwd, tool_name=self.name)
        if not resolved_cwd.exists():
            raise ToolError(f"bash cwd does not exist: {cwd}")
        if not resolved_cwd.is_dir():
            raise ToolError(f"bash cwd is not a directory: {cwd}")

        timeout = positive_int(
            payload.get("timeout_seconds", self.timeout_seconds),
            field_name="timeout_seconds",
            tool_name=self.name,
        )
        if timeout > self.timeout_seconds:
            raise ToolError(f"bash timeout_seconds cannot exceed {self.timeout_seconds}")

        try:
            completed = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                cwd=resolved_cwd,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolOutput(
                content=_render_timeout(
                    command=command,
                    cwd=resolved_cwd,
                    timeout=timeout,
                    exc=exc,
                ),
                is_error=True,
            )
        except OSError as exc:
            raise ToolError(f"bash tool failed to start: {exc}") from exc

        stdout, stdout_truncated = truncate_text(completed.stdout, max_chars=self.max_output_chars)
        stderr, stderr_truncated = truncate_text(completed.stderr, max_chars=self.max_output_chars)
        return ToolOutput(
            content=_render_completed(
                command=command,
                cwd=resolved_cwd,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            ),
            is_error=completed.returncode != 0,
        )


def _render_completed(
    *,
    command: str,
    cwd: Path,
    exit_code: int,
    stdout: str,
    stderr: str,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> str:
    return "\n".join(
        [
            f"command={command}",
            f"cwd={cwd}",
            f"exit_code={exit_code}",
            f"stdout_truncated={stdout_truncated}",
            "stdout:",
            stdout.rstrip(),
            f"stderr_truncated={stderr_truncated}",
            "stderr:",
            stderr.rstrip(),
        ]
    ).strip()


def _render_timeout(
    *,
    command: str,
    cwd: Path,
    timeout: int,
    exc: subprocess.TimeoutExpired,
) -> str:
    stdout = _decode_timeout_output(exc.stdout)
    stderr = _decode_timeout_output(exc.stderr)
    stdout, stdout_truncated = truncate_text(stdout, max_chars=DEFAULT_MAX_OUTPUT_CHARS)
    stderr, stderr_truncated = truncate_text(stderr, max_chars=DEFAULT_MAX_OUTPUT_CHARS)
    return "\n".join(
        [
            f"command={command}",
            f"cwd={cwd}",
            f"timeout_seconds={timeout}",
            "error=command timed out",
            f"stdout_truncated={stdout_truncated}",
            "stdout:",
            stdout.rstrip(),
            f"stderr_truncated={stderr_truncated}",
            "stderr:",
            stderr.rstrip(),
        ]
    ).strip()


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
