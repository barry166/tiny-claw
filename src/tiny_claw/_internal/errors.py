"""Shared errors and process exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    RUNTIME = 1
    USAGE = 2
    PROVIDER = 70
    TOOL = 71
    CONFIGURATION = 78
    INTERRUPTED = 130


class TinyClawError(Exception):
    exit_code = ExitCode.RUNTIME


class ConfigurationError(TinyClawError):
    exit_code = ExitCode.CONFIGURATION


class ProviderError(TinyClawError):
    exit_code = ExitCode.PROVIDER


class ToolError(TinyClawError):
    exit_code = ExitCode.TOOL
