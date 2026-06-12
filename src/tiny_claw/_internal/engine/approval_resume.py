"""Approval checkpoint resume flow for the engine."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from tiny_claw._internal.approval import (
    APPROVAL_METADATA_KEY,
    CHECKPOINT_DRAFT_METADATA_KEY,
    ApprovalRecord,
    FileRunCheckpointStore,
    RunCheckpoint,
    RunCheckpointDraft,
    render_rejected_observation,
)
from tiny_claw._internal.context import (
    ContextCompactor,
    PlanFiles,
    PlanMarkdownParser,
    PlanPromptBuilder,
    TodoItem,
    plan_step_status,
)
from tiny_claw._internal.engine import log_view
from tiny_claw._internal.engine.channel import Channel, NullChannel, notify_channel
from tiny_claw._internal.engine.observations import (
    append_tool_observations,
    approval_required_text,
)
from tiny_claw._internal.engine.run_policy import (
    phase_for_step,
    to_tool_choice,
    tool_policy_for_phase,
)
from tiny_claw._internal.engine.run_types import (
    STOP_REASON_APPROVAL_REQUIRED,
    STOP_REASON_APPROVAL_RESUME_FAILED,
    STOP_REASON_FINAL,
    STOP_REASON_MAX_STEPS_EXHAUSTED,
    STOP_REASON_TOOL_POLICY_BLOCKED,
    RunMode,
    RunResult,
    ToolPolicy,
)
from tiny_claw._internal.engine.tool_executor import ToolExecutor
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest, LLMResponse, ToolChoice
from tiny_claw._internal.provider.tracking import (
    ModelCallScope,
    clear_run_summary,
    model_call_scope,
)
from tiny_claw._internal.schema.message import Message, ToolCall, ToolCallResult, ToolDefinition
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.tools.registry import ToolRegistry
from tiny_claw._internal.tracing import NullTracer, SpanHandle, Tracer

logger = logging.getLogger(__name__)


ReturnResult = Callable[
    [
        str,
        str,
        str,
        int,
        int,
        SessionRef,
        RunMode,
        ToolPolicy,
        str | None,
        Channel,
        str | None,
        str | None,
        str | None,
        str | None,
        SpanHandle | None,
    ],
    RunResult,
]

RecordAndReturnResult = Callable[
    [
        FileMemoryStore,
        str,
        str,
        str,
        str,
        int,
        int,
        SessionRef,
        RunMode,
        str | None,
        ToolPolicy,
        str | None,
        Channel,
        str | None,
        str | None,
        str | None,
        SpanHandle | None,
    ],
    RunResult,
]


@dataclass(frozen=True)
class ApprovalResumeRunner:
    provider: LLMProvider
    context_compactor: ContextCompactor
    memory: SessionMemoryStore
    tools: ToolRegistry
    checkpoint_store: FileRunCheckpointStore | None
    return_result: ReturnResult
    record_and_return_result: RecordAndReturnResult
    tracer: Tracer = NullTracer()

    def resume_approved(
        self,
        *,
        approval: ApprovalRecord,
        session: SessionRef,
        channel: Channel | None = None,
    ) -> RunResult:
        resolved_channel = channel or NullChannel()
        checkpoint = self._read_resume_checkpoint(approval=approval, session=session)
        if checkpoint is None:
            return self._resume_failed_result(
                text=f"审批 {approval.id} 的 checkpoint 不存在或不属于当前会话，工具未执行。",
                session=session,
                channel=resolved_channel,
            )

        pending_call = _pending_tool_call(checkpoint)
        if pending_call is None:
            return self._resume_failed_result(
                text=f"审批 {approval.id} 的 checkpoint 中没有待恢复工具调用，工具未执行。",
                session=session,
                channel=resolved_channel,
            )

        run_id = uuid.uuid4().hex
        trace_root = self._begin_resume_trace(
            run_id=run_id,
            session=session,
            approval=approval,
            action="approve",
        )
        tool_executor = ToolExecutor(
            tools=self.tools,
            tracer=self.tracer,
            visible_tool_names=checkpoint.visible_tool_names,
        )
        batch = tool_executor.run_tool_batch(
            (pending_call,),
            channel=resolved_channel,
            session=session,
            workdir=session.workdir,
            context_metadata={APPROVAL_METADATA_KEY: approval.id},
        )
        if batch.suspended:
            response_text = approval_required_text(batch.observations)
            session_memory = self.memory.for_session(session)
            return self.record_and_return_result(
                session_memory,
                checkpoint.prompt,
                response_text,
                checkpoint.provider,
                STOP_REASON_APPROVAL_REQUIRED,
                checkpoint.step,
                checkpoint.max_steps,
                session,
                RunMode(checkpoint.mode),
                checkpoint.phase,
                ToolPolicy(checkpoint.tool_policy),
                _plan_text_for_session(session_memory),
                resolved_channel,
                batch.suspension.approval_id if batch.suspension is not None else None,
                batch.suspension.checkpoint_id if batch.suspension is not None else None,
                run_id,
                trace_root,
            )

        return self._continue_from_checkpoint(
            checkpoint=checkpoint,
            initial_observations=batch.observations,
            session=session,
            channel=resolved_channel,
            run_id=run_id,
            trace_root=trace_root,
        )

    def resume_rejected(
        self,
        *,
        approval: ApprovalRecord,
        session: SessionRef,
        channel: Channel | None = None,
    ) -> RunResult:
        resolved_channel = channel or NullChannel()
        checkpoint = self._read_resume_checkpoint(approval=approval, session=session)
        if checkpoint is None:
            return self._resume_failed_result(
                text=f"审批 {approval.id} 的 checkpoint 不存在或不属于当前会话。",
                session=session,
                channel=resolved_channel,
            )

        run_id = uuid.uuid4().hex
        trace_root = self._begin_resume_trace(
            run_id=run_id,
            session=session,
            approval=approval,
            action="reject",
        )
        observation = Message.tool_result(
            ToolCallResult(
                tool_call_id=approval.tool_call_id,
                name=approval.tool_name,
                content=render_rejected_observation(approval),
                is_error=True,
            )
        )
        metadata = dict(observation.metadata)
        metadata.update(
            {
                "approval_id": approval.id,
                "error_type": "approval_rejected",
            }
        )
        rejected_observation = Message(
            role=observation.role,
            content=observation.content,
            tool_calls=observation.tool_calls,
            tool_call_id=observation.tool_call_id,
            name=observation.name,
            metadata=metadata,
        )
        return self._continue_from_checkpoint(
            checkpoint=checkpoint,
            initial_observations=(rejected_observation,),
            session=session,
            channel=resolved_channel,
            run_id=run_id,
            trace_root=trace_root,
        )

    def preserve_prior_observations_for_suspension(
        self,
        observations: tuple[Message, ...],
        checkpoint_id: str | None,
        *,
        session: SessionRef,
    ) -> None:
        if self.checkpoint_store is None or checkpoint_id is None:
            return
        prior_observations = tuple(
            observation
            for observation in observations
            if observation.metadata.get("suspended") is not True
        )
        if not prior_observations:
            return
        try:
            checkpoint = self.checkpoint_store.read(
                session_key=session.key,
                checkpoint_id=checkpoint_id,
            )
        except (OSError, ValueError, KeyError):
            logger.warning("无法补写审批 checkpoint 的前置工具观察 checkpoint=%s", checkpoint_id)
            return
        updated = replace(
            checkpoint,
            messages=checkpoint.messages + prior_observations,
            current_step_had_tool_error=(
                checkpoint.current_step_had_tool_error
                or any(
                    observation.metadata.get("is_error") is True
                    for observation in prior_observations
                )
            ),
        )
        self.checkpoint_store.write(updated)

    def _continue_from_checkpoint(
        self,
        *,
        checkpoint: RunCheckpoint,
        initial_observations: tuple[Message, ...],
        session: SessionRef,
        channel: Channel,
        run_id: str,
        trace_root: SpanHandle,
    ) -> RunResult:
        session_memory = self.memory.for_session(session)
        plan_files = PlanFiles.from_session_root(session_memory.root)
        mode = RunMode(checkpoint.mode)
        plan_required = checkpoint.plan_required
        plan: str | None = None
        current_plan_todo: TodoItem | None = None
        if mode is RunMode.PLAN_ACT and plan_files.exists:
            snapshot = plan_files.read_snapshot()
            plan = snapshot.plan_text
            current_plan_todo = (
                _todo_by_id(
                    snapshot.todo_text,
                    checkpoint.current_plan_todo_id,
                )
                or snapshot.next_todo
            )

        messages = list(checkpoint.messages)
        current_step_had_tool_error = checkpoint.current_step_had_tool_error
        if append_tool_observations(messages, initial_observations):
            current_step_had_tool_error = True

        registered_tool_definitions = _visible_definitions(
            self.tools.definitions(),
            checkpoint.visible_tool_names,
        )
        tool_executor = ToolExecutor(
            tools=self.tools,
            tracer=self.tracer,
            visible_tool_names=checkpoint.visible_tool_names,
        )
        last_text = _last_message_text(messages)
        last_provider = checkpoint.provider
        tool_policy = ToolPolicy(checkpoint.tool_policy)

        for step in range(checkpoint.step + 1, checkpoint.max_steps + 1):
            phase = phase_for_step(mode=mode, step=step, plan_required=plan_required)
            tool_policy = tool_policy_for_phase(phase)
            request_tool_definitions = (
                registered_tool_definitions if tool_policy is ToolPolicy.AUTO else ()
            )
            step_span = self.tracer.begin_span(
                kind="agent.step",
                name=f"step.{step}",
                attributes={
                    "step": step,
                    "phase": phase,
                    "mode": mode.value,
                    "tool_policy": tool_policy.value,
                    "visible_tools": len(request_tool_definitions),
                    "resume_from_checkpoint_id": checkpoint.id,
                },
            )
            log_view.log_turn_start(logger, step=step, max_steps=checkpoint.max_steps, phase=phase)
            _notify_thinking(
                channel=channel,
                step=step,
                max_steps=checkpoint.max_steps,
                phase=phase,
            )
            log_view.log_model_request(
                logger,
                messages=len(messages),
                tool_choice=to_tool_choice(tool_policy).value,
                visible_tools=len(request_tool_definitions),
            )
            compaction = self.context_compactor.compact(messages)
            if compaction.changed or compaction.still_over_budget:
                log_view.log_context_compaction(
                    logger,
                    original_chars=compaction.original_chars,
                    compacted_chars=compaction.compacted_chars,
                    max_chars=compaction.max_chars,
                    masked_tool_results=compaction.masked_tool_results,
                    truncated_tool_results=compaction.truncated_tool_results,
                    still_over_budget=compaction.still_over_budget,
                )
                step_span.record_event(
                    "context.compacted",
                    {
                        "original_chars": compaction.original_chars,
                        "compacted_chars": compaction.compacted_chars,
                        "max_chars": compaction.max_chars,
                        "masked_tool_results": compaction.masked_tool_results,
                        "truncated_tool_results": compaction.truncated_tool_results,
                        "still_over_budget": compaction.still_over_budget,
                    },
                )
            response = self._complete_provider(
                messages=compaction.messages,
                tools=request_tool_definitions,
                max_steps=checkpoint.max_steps,
                tool_choice=to_tool_choice(tool_policy),
                session=session,
                run_id=run_id,
                mode=mode.value,
                phase=phase,
                step=step,
            )
            messages.append(response.message)
            last_text = response.text
            last_provider = response.provider
            step_span.set_attributes(
                {
                    "provider": response.provider,
                    "tool_calls": len(response.message.tool_calls),
                    "assistant_text_chars": len(response.text),
                }
            )

            log_view.log_model_response(
                logger,
                provider=response.provider,
                tool_calls=response.message.tool_calls,
                text=response.text,
                usage=response.usage,
            )

            if not response.message.tool_calls:
                if mode is RunMode.PLAN_ACT and current_plan_todo is not None:
                    status = plan_step_status(last_text)
                    if status == "completed" and not current_step_had_tool_error:
                        snapshot = plan_files.mark_done(current_plan_todo.id)
                        plan = snapshot.plan_text
                        if snapshot.next_todo is not None and step < checkpoint.max_steps:
                            current_plan_todo = snapshot.next_todo
                            current_step_had_tool_error = False
                            messages.append(
                                Message.user(
                                    PlanPromptBuilder.execute_current_task_prompt(snapshot)
                                )
                            )
                            self.tracer.end_span(step_span)
                            continue
                    elif status in {"blocked", "completed"}:
                        snapshot = plan_files.append_blocker(
                            todo=current_plan_todo,
                            reason=last_text,
                        )
                        plan = snapshot.plan_text
                return self.record_and_return_result(
                    session_memory,
                    checkpoint.prompt,
                    last_text,
                    last_provider,
                    STOP_REASON_FINAL,
                    step,
                    checkpoint.max_steps,
                    session,
                    mode,
                    phase,
                    tool_policy,
                    plan,
                    channel,
                    None,
                    None,
                    run_id,
                    trace_root,
                )

            if tool_policy is ToolPolicy.NONE:
                return self.record_and_return_result(
                    session_memory,
                    checkpoint.prompt,
                    last_text,
                    last_provider,
                    STOP_REASON_TOOL_POLICY_BLOCKED,
                    step,
                    checkpoint.max_steps,
                    session,
                    mode,
                    phase,
                    tool_policy,
                    plan,
                    channel,
                    None,
                    None,
                    run_id,
                    trace_root,
                )

            draft = RunCheckpointDraft(
                mode=mode.value,
                prompt=checkpoint.prompt,
                step=step,
                max_steps=checkpoint.max_steps,
                phase=phase,
                tool_policy=tool_policy.value,
                provider=last_provider,
                current_plan_todo_id=(
                    current_plan_todo.id if current_plan_todo is not None else None
                ),
                current_step_had_tool_error=current_step_had_tool_error,
                plan_required=plan_required,
                visible_tool_names=checkpoint.visible_tool_names,
                messages=tuple(messages),
                pending_tool_calls=response.message.tool_calls,
                pending_index=0,
            )
            batch = tool_executor.run_tool_batch(
                response.message.tool_calls,
                channel=channel,
                session=session,
                workdir=session.workdir,
                context_metadata={
                    CHECKPOINT_DRAFT_METADATA_KEY: draft,
                    "approval_requester": channel,
                },
            )
            if batch.suspended:
                self.preserve_prior_observations_for_suspension(
                    batch.observations,
                    batch.suspension.checkpoint_id if batch.suspension else None,
                    session=session,
                )
                response_text = approval_required_text(batch.observations)
                return self.record_and_return_result(
                    session_memory,
                    checkpoint.prompt,
                    response_text,
                    last_provider,
                    STOP_REASON_APPROVAL_REQUIRED,
                    step,
                    checkpoint.max_steps,
                    session,
                    mode,
                    phase,
                    tool_policy,
                    plan,
                    channel,
                    batch.suspension.approval_id if batch.suspension is not None else None,
                    batch.suspension.checkpoint_id if batch.suspension is not None else None,
                    run_id,
                    trace_root,
                )
            if append_tool_observations(messages, batch.observations):
                current_step_had_tool_error = True
            self.tracer.end_span(step_span)

        return self.record_and_return_result(
            session_memory,
            checkpoint.prompt,
            last_text,
            last_provider,
            STOP_REASON_MAX_STEPS_EXHAUSTED,
            checkpoint.max_steps,
            checkpoint.max_steps,
            session,
            mode,
            None,
            tool_policy,
            plan,
            channel,
            None,
            None,
            run_id,
            trace_root,
        )

    def _complete_provider(
        self,
        *,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
        max_steps: int,
        tool_choice: ToolChoice,
        session: SessionRef,
        run_id: str,
        mode: str,
        phase: str | None,
        step: int,
    ) -> LLMResponse:
        scope = ModelCallScope(
            session_key=session.key,
            session_source=session.source,
            session_display_name=session.display_name,
            run_id=run_id,
            mode=mode,
            phase=phase,
            step=step,
            max_steps=max_steps,
            caller="approval_resume",
        )
        with model_call_scope(scope):
            try:
                return self.provider.complete(
                    LLMRequest(
                        messages=messages,
                        tools=tools,
                        max_steps=max_steps,
                        tool_choice=tool_choice,
                    )
                )
            except Exception:
                clear_run_summary(run_id)
                self.tracer.end_trace(
                    status="error",
                    attributes={
                        "run_id": run_id,
                        "mode": mode,
                        "phase": phase,
                        "step": step,
                    },
                )
                raise

    def _read_resume_checkpoint(
        self,
        *,
        approval: ApprovalRecord,
        session: SessionRef,
    ) -> RunCheckpoint | None:
        if self.checkpoint_store is None:
            return None
        if approval.session_key != session.key:
            return None
        try:
            checkpoint = self.checkpoint_store.read(
                session_key=session.key,
                checkpoint_id=approval.checkpoint_id,
            )
        except (OSError, ValueError, KeyError):
            return None
        if checkpoint.session_key != session.key:
            return None
        return checkpoint

    def _resume_failed_result(
        self,
        *,
        text: str,
        session: SessionRef,
        channel: Channel,
    ) -> RunResult:
        return self.return_result(
            text,
            self.provider.name,
            STOP_REASON_APPROVAL_RESUME_FAILED,
            0,
            0,
            session,
            RunMode.ACT,
            ToolPolicy.AUTO,
            None,
            channel,
            None,
            None,
            None,
            None,
            None,
        )

    def _begin_resume_trace(
        self,
        *,
        run_id: str,
        session: SessionRef,
        approval: ApprovalRecord,
        action: str,
    ) -> SpanHandle:
        root = self.tracer.begin_trace(
            trace_id=run_id,
            session_key=session.key,
            session_source=session.source,
            kind="agent.run",
            name="tiny_claw.approval_resume",
            attributes={
                "run_id": run_id,
                "session_key": session.key,
                "session_source": session.source,
                "session_display_name": session.display_name,
                "mode": "approval_resume",
                "approval_id": approval.id,
                "checkpoint_id": approval.checkpoint_id,
                "approval_action": action,
                "tool_call_id": approval.tool_call_id,
                "tool_name": approval.tool_name,
                "workdir": str(session.workdir),
            },
        )
        resume_span = self.tracer.begin_span(
            kind="approval.resume",
            name=f"approval.{action}",
            attributes={
                "approval_id": approval.id,
                "checkpoint_id": approval.checkpoint_id,
                "approval_action": action,
                "tool_call_id": approval.tool_call_id,
                "tool_name": approval.tool_name,
            },
        )
        self.tracer.end_span(resume_span)
        return root


def _pending_tool_call(checkpoint: RunCheckpoint) -> ToolCall | None:
    if checkpoint.pending_index < 0:
        return None
    try:
        return checkpoint.pending_tool_calls[checkpoint.pending_index]
    except IndexError:
        return None


def _visible_definitions(
    definitions: tuple[ToolDefinition, ...],
    visible_tool_names: tuple[str, ...],
) -> tuple[ToolDefinition, ...]:
    visible = set(visible_tool_names)
    return tuple(definition for definition in definitions if definition.name in visible)


def _todo_by_id(todo_text: str, todo_id: str | None) -> TodoItem | None:
    if todo_id is None:
        return None
    for item in PlanMarkdownParser.items(todo_text):
        if item.id == todo_id:
            return item
    return None


def _plan_text_for_session(memory: FileMemoryStore) -> str | None:
    path = memory.root / "plan" / "PLAN.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _last_message_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.content:
            return message.content
    return ""


def _notify_thinking(
    *,
    channel: Channel,
    step: int,
    max_steps: int,
    phase: str,
) -> None:
    notify_channel(
        lambda: channel.on_thinking(
            step=step,
            max_steps=max_steps,
            phase=phase,
        )
    )
