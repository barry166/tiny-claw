"""Tool-call execution for ReAct turns."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from tiny_claw._internal.engine import log_view
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import Message, ToolCall, ToolCallResult
from tiny_claw._internal.tools.registry import ToolRegistry

PARALLEL_SAFE_TOOL_NAMES = {"read"}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolExecutor:
    tools: ToolRegistry
    max_parallel_tools: int = 4

    def __post_init__(self) -> None:
        if self.max_parallel_tools < 1:
            raise ValueError("max_parallel_tools must be greater than or equal to 1")

    def run_tool_calls(self, tool_calls: tuple[ToolCall, ...]) -> tuple[Message, ...]:
        observations: list[Message] = []
        parallel_group: list[ToolCall] = []

        for tool_call in tool_calls:
            if self._is_parallel_safe(tool_call):
                parallel_group.append(tool_call)
                continue

            observations.extend(self._run_parallel_group(tuple(parallel_group)))
            parallel_group.clear()
            observations.append(self._run_one(tool_call))

        observations.extend(self._run_parallel_group(tuple(parallel_group)))
        return tuple(observations)

    def _run_parallel_group(self, tool_calls: tuple[ToolCall, ...]) -> tuple[Message, ...]:
        if not tool_calls:
            return ()
        if len(tool_calls) == 1 or self.max_parallel_tools == 1:
            return tuple(self._run_one(tool_call) for tool_call in tool_calls)

        max_workers = min(self.max_parallel_tools, len(tool_calls))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            return tuple(executor.map(self._run_one, tool_calls))

    def _run_one(self, tool_call: ToolCall) -> Message:
        try:
            log_view.log_tool_call(logger, tool_call)
            output = self.tools.call(tool_call.name, tool_call.arguments)
            log_view.log_tool_result(logger, name=tool_call.name, output=output)
            result = ToolCallResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=output.content,
                is_error=output.is_error,
            )
        except ToolError as exc:
            log_view.log_tool_exception(logger, name=tool_call.name, error=str(exc))
            result = ToolCallResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=str(exc),
                is_error=True,
            )
        return result.to_message()

    def _is_parallel_safe(self, tool_call: ToolCall) -> bool:
        return tool_call.name in PARALLEL_SAFE_TOOL_NAMES
