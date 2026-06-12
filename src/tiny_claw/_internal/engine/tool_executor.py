"""Tool-call execution for ReAct turns."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Any

from tiny_claw._internal.engine import log_view
from tiny_claw._internal.engine.channel import Channel, NullChannel, notify_channel
from tiny_claw._internal.engine.tool_feedback import ToolErrorTranslator, tool_call_key
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import Message, ToolCall, ToolCallResult
from tiny_claw._internal.session import SessionRef
from tiny_claw._internal.tools.base import ToolOutput
from tiny_claw._internal.tools.middleware import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolSuspension,
)
from tiny_claw._internal.tools.registry import ToolRegistry
from tiny_claw._internal.tracing import NullTracer, Tracer, elapsed_ms

PARALLEL_SAFE_TOOL_NAMES = {"read"}
REPEAT_FAILURE_BLOCK_ATTEMPT = 3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRunBatch:
    observations: tuple[Message, ...]
    suspended: bool = False
    suspension: ToolSuspension | None = None


@dataclass(frozen=True)
class ToolExecutor:
    tools: ToolRegistry
    tracer: Tracer = field(default_factory=NullTracer)
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
        session: SessionRef | None = None,
        workdir: Path | None = None,
        context_metadata: dict[str, Any] | None = None,
    ) -> tuple[Message, ...]:
        return self.run_tool_batch(
            tool_calls,
            channel=channel,
            session=session,
            workdir=workdir,
            context_metadata=context_metadata,
        ).observations

    def run_tool_batch(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        channel: Channel | None = None,
        session: SessionRef | None = None,
        workdir: Path | None = None,
        context_metadata: dict[str, Any] | None = None,
    ) -> ToolRunBatch:
        resolved_channel = channel or NullChannel()
        resolved_session = session or _default_session()
        resolved_workdir = Path.cwd().resolve() if workdir is None else workdir.resolve()
        observations: list[Message] = []
        parallel_group: list[tuple[int, ToolCall]] = []

        for index, tool_call in enumerate(tool_calls):
            if self._is_parallel_safe(tool_call):
                parallel_group.append((index, tool_call))
                continue

            observations.extend(
                self._run_parallel_group(
                    tuple(parallel_group),
                    channel=resolved_channel,
                    session=resolved_session,
                    workdir=resolved_workdir,
                    context_metadata=context_metadata,
                )
            )
            parallel_group.clear()
            batch = self._run_one_batch(
                tool_call,
                channel=resolved_channel,
                session=resolved_session,
                workdir=resolved_workdir,
                metadata=_metadata_for_index(context_metadata, index),
            )
            observations.extend(batch.observations)
            if batch.suspended:
                return ToolRunBatch(
                    observations=tuple(observations),
                    suspended=True,
                    suspension=batch.suspension,
                )

        observations.extend(
            self._run_parallel_group(
                tuple(parallel_group),
                channel=resolved_channel,
                session=resolved_session,
                workdir=resolved_workdir,
                context_metadata=context_metadata,
            )
        )
        return ToolRunBatch(observations=tuple(observations))

    def _run_parallel_group(
        self,
        indexed_tool_calls: tuple[tuple[int, ToolCall], ...],
        *,
        channel: Channel,
        session: SessionRef,
        workdir: Path,
        context_metadata: dict[str, Any] | None,
    ) -> tuple[Message, ...]:
        if not indexed_tool_calls:
            return ()
        if len(indexed_tool_calls) == 1 or self.max_parallel_tools == 1:
            return tuple(
                self._run_one(
                    tool_call,
                    channel=channel,
                    session=session,
                    workdir=workdir,
                    metadata=_metadata_for_index(context_metadata, index),
                )
                for index, tool_call in indexed_tool_calls
            )

        for _, tool_call in indexed_tool_calls:
            notify_channel(partial(channel.on_tool_call, tool_call))
        max_workers = min(self.max_parallel_tools, len(indexed_tool_calls))
        trace_state = self.tracer.current_state()
        trace_parent_span_id = self.tracer.current_span_id()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            observations = tuple(
                executor.map(
                    lambda indexed_call: self._execute_one(
                        indexed_call[1],
                        session=session,
                        workdir=workdir,
                        metadata=_metadata_for_index(context_metadata, indexed_call[0]),
                        trace_state=trace_state,
                        trace_parent_span_id=trace_parent_span_id,
                    ),
                    indexed_tool_calls,
                )
            )
        for (_, tool_call), observation in zip(indexed_tool_calls, observations, strict=True):
            notify_channel(partial(channel.on_tool_result, call=tool_call, result=observation))
        return observations

    def _run_one(
        self,
        tool_call: ToolCall,
        *,
        channel: Channel,
        session: SessionRef,
        workdir: Path,
        metadata: dict[str, Any] | None,
    ) -> Message:
        return self._run_one_batch(
            tool_call,
            channel=channel,
            session=session,
            workdir=workdir,
            metadata=metadata,
        ).observations[0]

    def _run_one_batch(
        self,
        tool_call: ToolCall,
        *,
        channel: Channel,
        session: SessionRef,
        workdir: Path,
        metadata: dict[str, Any] | None,
    ) -> ToolRunBatch:
        notify_channel(partial(channel.on_tool_call, tool_call))
        observation = self._execute_one(
            tool_call,
            session=session,
            workdir=workdir,
            metadata=metadata,
            trace_state=None,
            trace_parent_span_id=None,
        )
        notify_channel(partial(channel.on_tool_result, call=tool_call, result=observation))
        if observation.metadata.get("suspended") is True:
            suspension_value = observation.metadata.get("_suspension")
            suspension = suspension_value if isinstance(suspension_value, ToolSuspension) else None
            return ToolRunBatch(
                observations=(observation,),
                suspended=True,
                suspension=suspension,
            )
        return ToolRunBatch(observations=(observation,))

    def _execute_one(
        self,
        tool_call: ToolCall,
        *,
        session: SessionRef,
        workdir: Path,
        metadata: dict[str, Any] | None,
        trace_state: Any | None = None,
        trace_parent_span_id: str | None = None,
    ) -> Message:
        started = time.perf_counter()
        attributes = {
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "session_key": session.key,
            "session_source": session.source,
        }
        if metadata and isinstance(metadata.get("tool_call_index"), int):
            attributes["tool_call_index"] = metadata["tool_call_index"]
        attributes.update(self.tracer.payload_attributes("tool_arguments", tool_call.arguments))
        span = self.tracer.begin_span(
            kind="tool.call",
            name=f"tool.{tool_call.name}",
            attributes=attributes,
            parent_span_id=trace_parent_span_id,
            state=trace_state,
        )
        try:
            message = self._execute_one_untraced(
                tool_call,
                session=session,
                workdir=workdir,
                metadata=metadata,
            )
        except Exception as exc:
            self.tracer.end_span(
                span,
                status="error",
                attributes={
                    "latency_ms": elapsed_ms(started),
                    "error_type": type(exc).__name__,
                },
            )
            raise

        is_error = message.metadata.get("is_error") is True
        end_attributes: dict[str, Any] = {
            "latency_ms": elapsed_ms(started),
            "is_error": is_error,
            "result_chars": len(message.content),
        }
        for key in (
            "approval_id",
            "checkpoint_id",
            "denied",
            "doom_loop_detected",
            "error_type",
            "retryable",
            "suspended",
            "suggested_tool",
        ):
            value = message.metadata.get(key)
            if value is not None:
                end_attributes[key] = value
        end_attributes.update(self.tracer.payload_attributes("tool_observation", message.content))
        if message.metadata.get("suspended") is True:
            pause_span = self.tracer.begin_span(
                kind="approval.pause",
                name="approval.pause",
                attributes={
                    "approval_id": message.metadata.get("approval_id"),
                    "checkpoint_id": message.metadata.get("checkpoint_id"),
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.name,
                    "reason": message.metadata.get("error_type"),
                },
                parent_span_id=span.span_id,
                state=span.state,
            )
            self.tracer.end_span(pause_span)
        self.tracer.end_span(
            span,
            status="error" if is_error else "ok",
            attributes=end_attributes,
        )
        return message

    def _execute_one_untraced(
        self,
        tool_call: ToolCall,
        *,
        session: SessionRef,
        workdir: Path,
        metadata: dict[str, Any] | None,
    ) -> Message:
        translator = ToolErrorTranslator(visible_tools=self._visible_tool_names())
        key = tool_call_key(tool_call)
        log_context = _tool_log_context(session)
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
            metadata_dict = dict(message.metadata)
            metadata_dict.update(translation.metadata(attempt_count=attempt_count))
            metadata_dict.update(
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
                context=log_context,
            )
            log_view.log_tool_result(
                logger,
                name=tool_call.name,
                output=output,
                context=log_context,
            )
            return Message(
                role=message.role,
                content=message.content,
                tool_calls=message.tool_calls,
                tool_call_id=message.tool_call_id,
                name=message.name,
                metadata=metadata_dict,
            )

        try:
            log_view.log_tool_call(logger, tool_call, context=log_context)
            execution = self.tools.execute(
                ToolExecutionContext(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    session=session,
                    workdir=workdir,
                    visible_tool_names=self._visible_tool_names(),
                    metadata=metadata or {},
                )
            )
            if execution.status == "suspended":
                return self._suspended_result(tool_call=tool_call, execution=execution)
            if execution.output is None:
                raise ToolError(f"{tool_call.name} middleware returned no tool output")
            output = execution.output
            log_view.log_tool_result(
                logger,
                name=tool_call.name,
                output=output,
                context=log_context,
            )
            if output.is_error:
                if execution.status == "denied":
                    message = self._denied_result(tool_call=tool_call, execution=execution)
                else:
                    message = self._error_result(
                        translator=translator,
                        tool_call=tool_call,
                        raw_error=output.content,
                        failure_key=key,
                        log_context=log_context,
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
            log_view.log_tool_exception(
                logger,
                name=tool_call.name,
                error=str(exc),
                context=log_context,
            )
            message = self._error_result(
                translator=translator,
                tool_call=tool_call,
                raw_error=str(exc),
                failure_key=key,
                log_context=log_context,
            )
        return message

    def _is_parallel_safe(self, tool_call: ToolCall) -> bool:
        return tool_call.name in PARALLEL_SAFE_TOOL_NAMES

    def _denied_result(
        self,
        *,
        tool_call: ToolCall,
        execution: ToolExecutionResult,
    ) -> Message:
        output = execution.output or ToolOutput(content="工具调用被拒绝。", is_error=True)
        message = ToolCallResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=output.content,
            is_error=True,
        ).to_message()
        metadata = dict(message.metadata)
        metadata.update(execution.metadata)
        return Message(
            role=message.role,
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=metadata,
        )

    def _suspended_result(
        self,
        *,
        tool_call: ToolCall,
        execution: ToolExecutionResult,
    ) -> Message:
        suspension = execution.suspension
        content = suspension.content if suspension is not None else "工具调用需要人工审批。"
        message = ToolCallResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=content,
            is_error=True,
        ).to_message()
        metadata = dict(message.metadata)
        metadata.update(execution.metadata)
        metadata.update(
            {
                "suspended": True,
                "error_type": "tool_approval_required",
            }
        )
        if suspension is not None:
            metadata.update(
                {
                    "approval_id": suspension.approval_id,
                    "checkpoint_id": suspension.checkpoint_id,
                    "_suspension": suspension,
                }
            )
        return Message(
            role=message.role,
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=metadata,
        )

    def _error_result(
        self,
        *,
        translator: ToolErrorTranslator,
        tool_call: ToolCall,
        raw_error: str,
        failure_key: str,
        log_context: str | None,
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
            context=log_context,
        )
        log_view.log_tool_result(
            logger,
            name=tool_call.name,
            output=output,
            context=log_context,
        )
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


def _default_session() -> SessionRef:
    cwd = Path.cwd().resolve()
    return SessionRef(
        key="tool-executor-default",
        source="internal",
        external_id="default",
        workdir=cwd,
        display_name="default",
    )


def _tool_log_context(session: SessionRef) -> str | None:
    if session.source != "subagent":
        return None
    return f"subagent_session={session.key}"


def _metadata_for_index(
    context_metadata: dict[str, Any] | None,
    index: int,
) -> dict[str, Any]:
    metadata = dict(context_metadata or {})
    metadata["tool_call_index"] = index
    return metadata
