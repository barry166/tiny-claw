"""Tool contracts shared by the engine and concrete tool implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.session import SessionRef


@dataclass(frozen=True)
class ToolInput:
    arguments: Mapping[str, Any]
    session: SessionRef | None = None
    workdir: Path | None = None
    visible_tool_names: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolOutput:
    content: str
    is_error: bool = False

    @property
    def output(self) -> str:
        return self.content


class Tool(Protocol):
    @property
    def name(self) -> str:
        """Unique tool name exposed to the model."""

    @property
    def description(self) -> str:
        """Human-readable tool description exposed to the model."""

    @property
    def parameters(self) -> Mapping[str, Any]:
        """JSON Schema style parameters exposed to the model."""

    def definition(self) -> ToolDefinition:
        """Return the provider-neutral tool definition."""

    def run(self, input: ToolInput) -> ToolOutput:
        """Run the tool with provider-neutral input."""


ToolResult = ToolOutput
