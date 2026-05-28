"""Tool registration and invocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import Tool, ToolInput, ToolOutput, ToolResult


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

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolOutput:
        return self.get(name).run(ToolInput(arguments=arguments))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition() for name in self.names())


__all__ = ["Tool", "ToolInput", "ToolOutput", "ToolRegistry", "ToolResult"]
