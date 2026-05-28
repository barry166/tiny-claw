"""Tool registration and invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tiny_claw._internal.errors import ToolError


@dataclass(frozen=True)
class ToolResult:
    output: str


class Tool(Protocol):
    @property
    def name(self) -> str:
        """Unique tool name."""

    @property
    def description(self) -> str:
        """Human-readable tool description."""

    def run(self, payload: Mapping[str, object]) -> ToolResult:
        """Run the tool with a structured payload."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name}") from exc

    def call(self, name: str, payload: Mapping[str, object]) -> ToolResult:
        return self.get(name).run(payload)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))
