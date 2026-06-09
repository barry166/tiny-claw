"""Chain-style middleware contracts for tool execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from tiny_claw._internal.session import SessionRef
from tiny_claw._internal.tools.base import ToolOutput

ToolExecutionStatus = Literal["completed", "denied", "suspended"]


@dataclass(frozen=True)
class ToolSuspension:
    approval_id: str
    checkpoint_id: str
    reason: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolExecutionStatus
    output: ToolOutput | None = None
    suspension: ToolSuspension | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def completed(
        cls,
        output: ToolOutput,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(status="completed", output=output, metadata=metadata or {})

    @classmethod
    def denied(
        cls,
        content: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(
            status="denied",
            output=ToolOutput(content=content, is_error=True),
            metadata=metadata or {},
        )

    @classmethod
    def suspended(
        cls,
        suspension: ToolSuspension,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(status="suspended", suspension=suspension, metadata=metadata or {})


@dataclass(frozen=True)
class ToolExecutionContext:
    tool_call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    session: SessionRef
    workdir: Path
    visible_tool_names: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


ToolNext = Callable[[ToolExecutionContext], ToolExecutionResult]


class ToolMiddleware(Protocol):
    def __call__(self, ctx: ToolExecutionContext, next: ToolNext) -> ToolExecutionResult:
        """Run middleware logic and optionally call the next execution step."""
