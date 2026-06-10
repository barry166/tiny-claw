"""Tool registration and invocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import Tool, ToolInput, ToolOutput, ToolResult
from tiny_claw._internal.tools.middleware import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolMiddleware,
    ToolNext,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._middlewares: list[ToolMiddleware] = []

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

    def use(self, middleware: ToolMiddleware) -> None:
        self._middlewares.append(middleware)

    def execute(self, ctx: ToolExecutionContext) -> ToolExecutionResult:
        def terminal(current: ToolExecutionContext) -> ToolExecutionResult:
            output = self.get(current.tool_name).run(
                ToolInput(
                    arguments=current.arguments,
                    session=current.session,
                    workdir=current.workdir,
                    visible_tool_names=current.visible_tool_names,
                    metadata=current.metadata,
                )
            )
            return ToolExecutionResult.completed(output)

        next_step: ToolNext = terminal
        for middleware in reversed(self._middlewares):
            previous_next = next_step

            def wrapped(
                current: ToolExecutionContext,
                *,
                current_middleware: ToolMiddleware = middleware,
                current_next: ToolNext = previous_next,
            ) -> ToolExecutionResult:
                return current_middleware(current, current_next)

            next_step = wrapped
        return next_step(ctx)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition() for name in self.names())


__all__ = ["Tool", "ToolInput", "ToolOutput", "ToolRegistry", "ToolResult"]
