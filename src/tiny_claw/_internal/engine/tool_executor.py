"""Tool-call execution for ReAct turns."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from threading import Lock

from tiny_claw._internal.engine import log_view
from tiny_claw._internal.engine.channel import Channel, NullChannel, notify_channel
from tiny_claw._internal.engine.tool_feedback import ToolErrorTranslator, tool_call_key
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import Message, ToolCall, ToolCallResult
from tiny_claw._internal.tools.base import ToolOutput
from tiny_claw._internal.tools.registry import ToolRegistry

PARALLEL_SAFE_TOOL_NAMES = {"read"}
REPEAT_FAILURE_BLOCK_ATTEMPT = 3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolExecutor:
    tools: ToolRegistry
    max_parallel_tools: int = 4
    visible_tool_names: tuple[str, ...] | None = None
    repeat_failure_block_attempt: int = REPEAT_FAILURE_BLOCK_ATTEMPT
    _failure_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _failure_counts_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_parallel_tools < 1:
            raise ValueError("max_parallel_tools must be greater than or equal to 1")
        if self.repeat_failure_block_attempt < 2:
            raise ValueError("repeat_failure_block_attempt must be greater than or equal to 2")

    def run_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        channel: Channel | None = None,
    ) -> tuple[Message, ...]:
        resolved_channel = channel or NullChannel()
        observations: list[Message] = []
        parallel_group: list[ToolCall] = []

        for tool_call in tool_calls:
            if self._is_parallel_safe(tool_call):
                parallel_group.append(tool_call)
                continue

            observations.extend(
                self._run_parallel_group(tuple(parallel_group), channel=resolved_channel)
            )
            parallel_group.clear()
            observations.append(self._run_one(tool_call, channel=resolved_channel))

        observations.extend(
            self._run_parallel_group(tuple(parallel_group), channel=resolved_channel)
        )
        return tuple(observations)

    def _run_parallel_group(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        channel: Channel,
    ) -> tuple[Message, ...]:
        if not tool_calls:
            return ()
        if len(tool_calls) == 1 or self.max_parallel_tools == 1:
            return tuple(self._run_one(tool_call, channel=channel) for tool_call in tool_calls)

        for tool_call in tool_calls:
            notify_channel(partial(channel.on_tool_call, tool_call))
        max_workers = min(self.max_parallel_tools, len(tool_calls))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            observations = tuple(executor.map(self._execute_one, tool_calls))
        for tool_call, observation in zip(tool_calls, observations, strict=True):
            notify_channel(partial(channel.on_tool_result, call=tool_call, result=observation))
        return observations

    def _run_one(self, tool_call: ToolCall, *, channel: Channel) -> Message:
        notify_channel(partial(channel.on_tool_call, tool_call))
        observation = self._execute_one(tool_call)
        notify_channel(partial(channel.on_tool_result, call=tool_call, result=observation))
        return observation

    def _execute_one(self, tool_call: ToolCall) -> Message:
        translator = ToolErrorTranslator(visible_tools=self._visible_tool_names())
        key = tool_call_key(tool_call)
        current_failures = self._failure_count(key)
        if current_failures + 1 >= self.repeat_failure_block_attempt:
            attempt_count = current_failures + 1
            translation = translator.repeat_call_blocked(
                tool_call=tool_call,
                attempt_count=attempt_count,
            )
            self._record_failure(key)
            result = ToolCallResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=translation.render(
                    raw_error="同一个工具和同一组参数已经连续失败。",
                    attempt_count=attempt_count,
                ),
                is_error=True,
            )
            message = result.to_message()
            metadata = dict(message.metadata)
            metadata.update(translation.metadata(attempt_count=attempt_count))
            metadata.update(
                {
                    "doom_loop_detected": True,
                    "doom_loop_tool": tool_call.name,
                }
            )
            output = ToolOutput(content=result.content, is_error=True)
            log_view.log_tool_error_fallback(
                logger,
                name=tool_call.name,
                error_type=translation.error_type,
                attempt_count=attempt_count,
                retryable=translation.retryable,
                suggested_tool=translation.suggested_tool,
            )
            log_view.log_tool_result(logger, name=tool_call.name, output=output)
            return Message(
                role=message.role,
                content=message.content,
                tool_calls=message.tool_calls,
                tool_call_id=message.tool_call_id,
                name=message.name,
                metadata=metadata,
            )

        try:
            log_view.log_tool_call(logger, tool_call)
            output = self.tools.call(tool_call.name, tool_call.arguments)
            log_view.log_tool_result(logger, name=tool_call.name, output=output)
            if output.is_error:
                message = self._error_result(
                    translator=translator,
                    tool_call=tool_call,
                    raw_error=output.content,
                    failure_key=key,
                )
            else:
                self._clear_failure(key)
                message = ToolCallResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=output.content,
                    is_error=False,
                ).to_message()
        except ToolError as exc:
            log_view.log_tool_exception(logger, name=tool_call.name, error=str(exc))
            message = self._error_result(
                translator=translator,
                tool_call=tool_call,
                raw_error=str(exc),
                failure_key=key,
            )
        return message

    def _is_parallel_safe(self, tool_call: ToolCall) -> bool:
        return tool_call.name in PARALLEL_SAFE_TOOL_NAMES

    def _error_result(
        self,
        *,
        translator: ToolErrorTranslator,
        tool_call: ToolCall,
        raw_error: str,
        failure_key: str,
    ) -> Message:
        attempt_count = self._record_failure(failure_key)
        translation = translator.translate(
            tool_call=tool_call,
            raw_error=raw_error,
            attempt_count=attempt_count,
        )
        output = ToolOutput(
            content=translation.render(raw_error=raw_error, attempt_count=attempt_count),
            is_error=True,
        )
        log_view.log_tool_error_fallback(
            logger,
            name=tool_call.name,
            error_type=translation.error_type,
            attempt_count=attempt_count,
            retryable=translation.retryable,
            suggested_tool=translation.suggested_tool,
        )
        log_view.log_tool_result(logger, name=tool_call.name, output=output)
        message = ToolCallResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=output.content,
            is_error=True,
        ).to_message()
        metadata = dict(message.metadata)
        metadata.update(translation.metadata(attempt_count=attempt_count))
        return Message(
            role=message.role,
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=metadata,
        )

    def _visible_tool_names(self) -> tuple[str, ...]:
        if self.visible_tool_names is not None:
            return self.visible_tool_names
        return self.tools.names()

    def _record_failure(self, key: str) -> int:
        with self._failure_counts_lock:
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
            return self._failure_counts[key]

    def _clear_failure(self, key: str) -> None:
        with self._failure_counts_lock:
            self._failure_counts.pop(key, None)

    def _failure_count(self, key: str) -> int:
        with self._failure_counts_lock:
            return self._failure_counts.get(key, 0)
