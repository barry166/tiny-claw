"""Core application main loop."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from tiny_claw._internal.approval import (
    CHECKPOINT_DRAFT_METADATA_KEY,
    ApprovalRecord,
    FileRunCheckpointStore,
    RunCheckpointDraft,
)
from tiny_claw._internal.context import (
    ContextBuilder,
    ContextCompactor,
    PlanFiles,
    PlanPromptBuilder,
    PlanResponseParser,
    TodoItem,
    plan_step_status,
)
from tiny_claw._internal.engine import log_view
from tiny_claw._internal.engine.approval_resume import ApprovalResumeRunner
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
from tiny_claw._internal.provider.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ToolChoice,
)
from tiny_claw._internal.provider.tracking import (
    ModelCallScope,
    clear_run_summary,
    log_run_summary,
    model_call_scope,
)
from tiny_claw._internal.schema.message import Message, ToolDefinition
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.tools.registry import ToolRegistry
from tiny_claw._internal.tracing import NullTracer, SpanHandle, Tracer

logger = logging.getLogger(__name__)

__all__ = [
    "STOP_REASON_APPROVAL_REQUIRED",
    "STOP_REASON_APPROVAL_RESUME_FAILED",
    "STOP_REASON_FINAL",
    "STOP_REASON_MAX_STEPS_EXHAUSTED",
    "STOP_REASON_TOOL_POLICY_BLOCKED",
    "MainLoop",
    "RunMode",
    "RunResult",
    "ToolPolicy",
]


@dataclass(frozen=True)
class MainLoop:
    provider: LLMProvider
    context_builder: ContextBuilder
    context_compactor: ContextCompactor
    memory: SessionMemoryStore
    tools: ToolRegistry
    checkpoint_store: FileRunCheckpointStore | None = None
    tracer: Tracer = NullTracer()

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def run(
        self,
        *,
        prompt: str,
        max_steps: int = 20,
        mode: RunMode = RunMode.ACT,
        session: SessionRef,
        channel: Channel | None = None,
    ) -> RunResult:
        if max_steps < 1:
            raise ValueError("max_steps must be greater than or equal to 1")

        run_id = uuid.uuid4().hex
        trace_root = self.tracer.begin_trace(
            trace_id=run_id,
            session_key=session.key,
            session_source=session.source,
            kind="agent.run",
            name="tiny_claw.run",
            attributes={
                "run_id": run_id,
                "session_key": session.key,
                "session_source": session.source,
                "session_display_name": session.display_name,
                "mode": mode.value,
                "max_steps": max_steps,
                "workdir": str(session.workdir),
            },
        )
        resolved_channel = channel or NullChannel()
        notify_channel(
            partial(
                resolved_channel.on_start,
                prompt=prompt,
                mode=mode.value,
                max_steps=max_steps,
            )
        )
        session_memory = self.memory.for_session(session)
        recent_memory = session_memory.read_recent(limit=5)
        plan_files = PlanFiles.from_session_root(session_memory.root)
        if mode is RunMode.PLAN:
            return self._run_plan_mode(
                prompt=prompt,
                max_steps=max_steps,
                session=session,
                memory=session_memory,
                plan_files=plan_files,
                channel=resolved_channel,
                run_id=run_id,
                trace_root=trace_root,
            )
        context = self.context_builder.build(
            prompt=prompt,
            memories=recent_memory,
            workdir=session.workdir,
        )
        messages = list(context.messages)
        registered_tool_definitions = self.tools.definitions()
        if context.allowed_tools is not None:
            allowed_tools = set(context.allowed_tools)
            registered_tool_definitions = tuple(
                definition
                for definition in registered_tool_definitions
                if definition.name in allowed_tools
            )
        tool_executor = ToolExecutor(
            tools=self.tools,
            tracer=self.tracer,
            visible_tool_names=tuple(definition.name for definition in registered_tool_definitions),
        )
        last_text = ""
        last_provider = self.provider.name
        plan: str | None = None
        plan_required = mode is RunMode.PLAN_ACT and not plan_files.exists
        current_plan_todo: TodoItem | None = None
        current_step_had_tool_error = False

        log_view.log_run_start(
            logger,
            provider=self.provider.name,
            mode=mode.value,
            max_steps=max_steps,
            workdir=session.workdir,
            session_key=session.key,
            session_source=session.source,
            registered_tools=len(registered_tool_definitions),
            memories=len(recent_memory),
        )
        if context.selected_skills:
            logger.info(
                "上下文技能已加载 skills=%s allowed_tools=%s",
                ",".join(skill.name for skill in context.selected_skills),
                ",".join(context.allowed_tools or ()),
            )
        if mode is RunMode.THINK:
            logger.info("思考模式已启用：本次请求不会向模型暴露工具定义，也不会执行工具调用")
        if mode is RunMode.PLAN_ACT:
            logger.info("plan-act 模式已启用：第一轮规划隐藏工具，后续执行阶段暴露工具")
            if plan_required:
                messages.append(Message.system(PlanPromptBuilder.create_system_prompt()))
            else:
                snapshot = plan_files.read_snapshot()
                plan = snapshot.plan_text
                current_plan_todo = snapshot.next_todo
                if current_plan_todo is None:
                    return self._return_result(
                        text=PlanPromptBuilder.render_all_done(snapshot),
                        provider=last_provider,
                        stop_reason=STOP_REASON_FINAL,
                        steps=0,
                        max_steps=max_steps,
                        session=session,
                        mode=mode,
                        tool_policy=ToolPolicy.AUTO,
                        plan=plan,
                        channel=resolved_channel,
                        run_id=run_id,
                        trace_root=trace_root,
                    )
                messages.append(
                    Message.user(PlanPromptBuilder.execute_current_task_prompt(snapshot))
                )

        for step in range(1, max_steps + 1):
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
                    "message_count": len(messages),
                },
            )
            log_view.log_turn_start(logger, step=step, max_steps=max_steps, phase=phase)
            if mode is RunMode.PLAN_ACT and phase == "plan":
                logger.info(
                    "[Plan] 规划阶段开始 step=%s/%s visible_tools=0",
                    step,
                    max_steps,
                )
            self._notify_thinking(
                channel=resolved_channel,
                step=step,
                max_steps=max_steps,
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
                max_steps=max_steps,
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
            tool_call_count = len(response.message.tool_calls)
            step_span.set_attributes(
                {
                    "provider": response.provider,
                    "tool_calls": tool_call_count,
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

            if mode is RunMode.PLAN_ACT and phase == "plan":
                if response.message.tool_calls:
                    logger.warning(
                        "规划阶段收到工具调用 tool_calls=%s，已阻止进入执行阶段",
                        tool_call_count,
                    )
                    return self._record_and_return_result(
                        memory=session_memory,
                        prompt=prompt,
                        response=last_text,
                        provider=last_provider,
                        stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                        steps=step,
                        max_steps=max_steps,
                        session=session,
                        run_mode=mode,
                        phase=phase,
                        tool_policy=tool_policy,
                        plan=plan,
                        channel=resolved_channel,
                        run_id=run_id,
                        trace_root=trace_root,
                    )

                plan_text, todo_text = PlanResponseParser.parse_create_response(
                    response_text=last_text,
                    prompt=prompt,
                )
                snapshot = plan_files.create_missing(plan_text=plan_text, todo_text=todo_text)
                plan = snapshot.plan_text
                last_text = PlanPromptBuilder.render_plan_result(snapshot)
                logger.info(
                    "[Plan] 规划阶段完成 step=%s plan_chars=%s",
                    step,
                    len(plan),
                )

                if step == max_steps:
                    logger.warning(
                        "主循环结束 reason=%s steps=%s provider=%s mode=%s phase=%s",
                        STOP_REASON_MAX_STEPS_EXHAUSTED,
                        max_steps,
                        last_provider,
                        mode.value,
                        phase,
                    )
                    return self._record_and_return_result(
                        memory=session_memory,
                        prompt=prompt,
                        response=last_text,
                        provider=last_provider,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                        steps=step,
                        max_steps=max_steps,
                        session=session,
                        run_mode=mode,
                        phase=phase,
                        tool_policy=tool_policy,
                        plan=plan,
                        channel=resolved_channel,
                        run_id=run_id,
                        trace_root=trace_root,
                    )

                messages.append(
                    Message.user(PlanPromptBuilder.execute_current_task_prompt(snapshot))
                )
                current_plan_todo = snapshot.next_todo
                current_step_had_tool_error = False
                logger.info(
                    "[Plan] 进入执行阶段 next_step=%s visible_tools=%s",
                    step + 1,
                    len(registered_tool_definitions),
                )
                self.tracer.end_span(step_span)
                continue

            if not response.message.tool_calls:
                if mode is RunMode.PLAN_ACT and current_plan_todo is not None:
                    status = plan_step_status(last_text)
                    if status == "completed" and not current_step_had_tool_error:
                        snapshot = plan_files.mark_done(current_plan_todo.id)
                        plan = snapshot.plan_text
                        if snapshot.next_todo is not None and step < max_steps:
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
                return self._record_and_return_result(
                    memory=session_memory,
                    prompt=prompt,
                    response=last_text,
                    provider=last_provider,
                    stop_reason=STOP_REASON_FINAL,
                    steps=step,
                    max_steps=max_steps,
                    session=session,
                    run_mode=mode,
                    phase=phase,
                    tool_policy=tool_policy,
                    plan=plan,
                    channel=resolved_channel,
                    run_id=run_id,
                    trace_root=trace_root,
                )

            if tool_policy is ToolPolicy.NONE:
                logger.warning(
                    "模型在禁用工具策略下仍返回工具调用 tool_calls=%s，已阻止执行",
                    tool_call_count,
                )
                return self._record_and_return_result(
                    memory=session_memory,
                    prompt=prompt,
                    response=last_text,
                    provider=last_provider,
                    stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                    steps=step,
                    max_steps=max_steps,
                    session=session,
                    run_mode=mode,
                    phase=phase,
                    tool_policy=tool_policy,
                    plan=plan,
                    channel=resolved_channel,
                    run_id=run_id,
                    trace_root=trace_root,
                )

            draft = RunCheckpointDraft(
                mode=mode.value,
                prompt=prompt,
                step=step,
                max_steps=max_steps,
                phase=phase,
                tool_policy=tool_policy.value,
                provider=last_provider,
                current_plan_todo_id=(
                    current_plan_todo.id if current_plan_todo is not None else None
                ),
                current_step_had_tool_error=current_step_had_tool_error,
                plan_required=plan_required,
                visible_tool_names=tuple(
                    definition.name for definition in registered_tool_definitions
                ),
                messages=tuple(messages),
                pending_tool_calls=response.message.tool_calls,
                pending_index=0,
            )
            batch = tool_executor.run_tool_batch(
                response.message.tool_calls,
                channel=resolved_channel,
                session=session,
                workdir=session.workdir,
                context_metadata={
                    CHECKPOINT_DRAFT_METADATA_KEY: draft,
                    "approval_requester": resolved_channel,
                },
            )
            if batch.suspended:
                self._preserve_prior_observations_for_suspension(
                    batch.observations,
                    batch.suspension.checkpoint_id if batch.suspension else None,
                    session=session,
                )
                response_text = approval_required_text(batch.observations)
                return self._record_and_return_result(
                    memory=session_memory,
                    prompt=prompt,
                    response=response_text,
                    provider=last_provider,
                    stop_reason=STOP_REASON_APPROVAL_REQUIRED,
                    steps=step,
                    max_steps=max_steps,
                    session=session,
                    run_mode=mode,
                    phase=phase,
                    tool_policy=tool_policy,
                    plan=plan,
                    channel=resolved_channel,
                    approval_id=(
                        batch.suspension.approval_id if batch.suspension is not None else None
                    ),
                    checkpoint_id=(
                        batch.suspension.checkpoint_id if batch.suspension is not None else None
                    ),
                    run_id=run_id,
                    trace_root=trace_root,
                )
            if append_tool_observations(messages, batch.observations):
                current_step_had_tool_error = True
            self.tracer.end_span(step_span)

        return self._record_and_return_result(
            memory=session_memory,
            prompt=prompt,
            response=last_text,
            provider=last_provider,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            steps=max_steps,
            max_steps=max_steps,
            session=session,
            run_mode=mode,
            phase=None,
            tool_policy=tool_policy,
            plan=plan,
            channel=resolved_channel,
            run_id=run_id,
            trace_root=trace_root,
        )

    def _run_plan_mode(
        self,
        *,
        prompt: str,
        max_steps: int,
        session: SessionRef,
        memory: FileMemoryStore,
        plan_files: PlanFiles,
        channel: Channel,
        run_id: str,
        trace_root: SpanHandle,
    ) -> RunResult:
        step_span = self.tracer.begin_span(
            kind="agent.step",
            name="step.1",
            attributes={
                "step": 1,
                "phase": "plan",
                "mode": RunMode.PLAN.value,
                "tool_policy": ToolPolicy.NONE.value,
                "visible_tools": 0,
            },
        )
        self._notify_thinking(channel=channel, step=1, max_steps=max_steps, phase="plan")
        if plan_files.exists:
            snapshot = plan_files.read_snapshot()
            text = PlanPromptBuilder.render_plan_result(snapshot)
            self._record_run(
                memory=memory,
                prompt=prompt,
                response=PlanPromptBuilder.render_plan_memory(snapshot, plan_files=plan_files),
            )
            return self._return_result(
                text=text,
                provider=self.provider.name,
                stop_reason=STOP_REASON_FINAL,
                steps=1,
                max_steps=max_steps,
                session=session,
                mode=RunMode.PLAN,
                tool_policy=ToolPolicy.NONE,
                plan=snapshot.plan_text,
                channel=channel,
                run_id=run_id,
                trace_root=trace_root,
            )

        context = self.context_builder.build(
            prompt=prompt,
            memories=memory.read_recent(limit=5),
            workdir=session.workdir,
        )
        messages = list(context.messages)
        messages.append(Message.system(PlanPromptBuilder.create_system_prompt()))
        compaction = self.context_compactor.compact(messages)
        response = self._complete_provider(
            messages=compaction.messages,
            tools=(),
            max_steps=max_steps,
            tool_choice=ToolChoice.NONE,
            session=session,
            run_id=run_id,
            mode=RunMode.PLAN.value,
            phase="plan",
            step=1,
        )
        step_span.set_attributes(
            {
                "provider": response.provider,
                "tool_calls": len(response.message.tool_calls),
                "assistant_text_chars": len(response.text),
            }
        )
        if response.message.tool_calls:
            text = response.text
            self._record_run(memory=memory, prompt=prompt, response=text)
            return self._return_result(
                text=text,
                provider=response.provider,
                stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                steps=1,
                max_steps=max_steps,
                session=session,
                mode=RunMode.PLAN,
                tool_policy=ToolPolicy.NONE,
                plan=text,
                channel=channel,
                run_id=run_id,
                trace_root=trace_root,
            )

        plan_text, todo_text = PlanResponseParser.parse_create_response(
            response_text=response.text,
            prompt=prompt,
        )
        snapshot = plan_files.create_missing(plan_text=plan_text, todo_text=todo_text)
        text = PlanPromptBuilder.render_plan_result(snapshot)
        self._record_run(memory=memory, prompt=prompt, response=text)
        return self._return_result(
            text=text,
            provider=response.provider,
            stop_reason=STOP_REASON_FINAL,
            steps=1,
            max_steps=max_steps,
            session=session,
            mode=RunMode.PLAN,
            tool_policy=ToolPolicy.NONE,
            plan=snapshot.plan_text,
            channel=channel,
            run_id=run_id,
            trace_root=trace_root,
        )

    def resume_approved_approval(
        self,
        *,
        approval: ApprovalRecord,
        session: SessionRef,
        channel: Channel | None = None,
    ) -> RunResult:
        return self._approval_resume_runner().resume_approved(
            approval=approval,
            session=session,
            channel=channel,
        )

    def resume_rejected_approval(
        self,
        *,
        approval: ApprovalRecord,
        session: SessionRef,
        channel: Channel | None = None,
    ) -> RunResult:
        return self._approval_resume_runner().resume_rejected(
            approval=approval,
            session=session,
            channel=channel,
        )

    def _preserve_prior_observations_for_suspension(
        self,
        observations: tuple[Message, ...],
        checkpoint_id: str | None,
        *,
        session: SessionRef,
    ) -> None:
        self._approval_resume_runner().preserve_prior_observations_for_suspension(
            observations,
            checkpoint_id,
            session=session,
        )

    def _approval_resume_runner(self) -> ApprovalResumeRunner:
        return ApprovalResumeRunner(
            provider=self.provider,
            context_compactor=self.context_compactor,
            memory=self.memory,
            tools=self.tools,
            checkpoint_store=self.checkpoint_store,
            return_result=self._return_result_from_runner,
            record_and_return_result=self._record_and_return_result_from_runner,
            tracer=self.tracer,
        )

    def _return_result_from_runner(
        self,
        text: str,
        provider: str,
        stop_reason: str,
        steps: int,
        max_steps: int,
        session: SessionRef,
        mode: RunMode,
        tool_policy: ToolPolicy,
        plan: str | None,
        channel: Channel,
        phase: str | None,
        approval_id: str | None,
        checkpoint_id: str | None,
        run_id: str | None,
        trace_root: SpanHandle | None = None,
    ) -> RunResult:
        return self._return_result(
            text=text,
            provider=provider,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
            session=session,
            mode=mode,
            tool_policy=tool_policy,
            plan=plan,
            channel=channel,
            phase=phase,
            approval_id=approval_id,
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            trace_root=trace_root,
        )

    def _record_and_return_result_from_runner(
        self,
        memory: FileMemoryStore,
        prompt: str,
        response: str,
        provider: str,
        stop_reason: str,
        steps: int,
        max_steps: int,
        session: SessionRef,
        run_mode: RunMode,
        phase: str | None,
        tool_policy: ToolPolicy,
        plan: str | None,
        channel: Channel,
        approval_id: str | None,
        checkpoint_id: str | None,
        run_id: str | None,
        trace_root: SpanHandle | None = None,
    ) -> RunResult:
        return self._record_and_return_result(
            memory=memory,
            prompt=prompt,
            response=response,
            provider=provider,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
            session=session,
            run_mode=run_mode,
            phase=phase,
            tool_policy=tool_policy,
            plan=plan,
            channel=channel,
            approval_id=approval_id,
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            trace_root=trace_root,
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
            caller="main_loop",
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

    def _return_result(
        self,
        *,
        text: str,
        provider: str,
        stop_reason: str,
        steps: int,
        max_steps: int,
        session: SessionRef,
        mode: RunMode,
        tool_policy: ToolPolicy,
        plan: str | None,
        channel: Channel,
        phase: str | None = None,
        approval_id: str | None = None,
        checkpoint_id: str | None = None,
        run_id: str | None = None,
        trace_root: SpanHandle | None = None,
    ) -> RunResult:
        log_view.log_run_complete(
            logger,
            provider=provider,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
            mode=mode.value,
            phase=phase,
            tool_policy=tool_policy.value,
        )
        log_view.log_run_return(
            logger,
            text=text,
            provider=provider,
            stop_reason=stop_reason,
        )
        if run_id is not None:
            log_run_summary(logger, run_id=run_id)
            clear_run_summary(run_id)
        trace_id: str | None = None
        trace_path: Path | None = None
        if trace_root is not None:
            trace_root.set_attributes(
                {
                    "provider": provider,
                    "stop_reason": stop_reason,
                    "steps": steps,
                    "max_steps": max_steps,
                    "mode": mode.value,
                    "phase": phase,
                    "tool_policy": tool_policy.value,
                    "approval_id": approval_id,
                    "checkpoint_id": checkpoint_id,
                    "result_text_chars": len(text),
                }
            )
            record_info = self.tracer.end_trace(
                status="error"
                if stop_reason
                in {
                    STOP_REASON_APPROVAL_RESUME_FAILED,
                    STOP_REASON_TOOL_POLICY_BLOCKED,
                }
                else "ok"
            )
            if record_info is not None:
                trace_id = record_info.trace_id
                trace_path = record_info.path
        self._notify_done(
            channel=channel,
            text=text,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
        )
        return RunResult(
            text=text,
            provider=provider,
            steps=steps,
            max_steps=max_steps,
            workdir=session.workdir,
            stop_reason=stop_reason,
            mode=mode,
            tool_policy=tool_policy,
            plan=plan,
            approval_id=approval_id,
            checkpoint_id=checkpoint_id,
            trace_id=trace_id,
            trace_path=trace_path,
        )

    def _record_and_return_result(
        self,
        *,
        memory: FileMemoryStore,
        prompt: str,
        response: str,
        provider: str,
        stop_reason: str,
        steps: int,
        max_steps: int,
        session: SessionRef,
        run_mode: RunMode,
        phase: str | None,
        tool_policy: ToolPolicy,
        plan: str | None,
        channel: Channel,
        approval_id: str | None = None,
        checkpoint_id: str | None = None,
        run_id: str | None = None,
        trace_root: SpanHandle | None = None,
    ) -> RunResult:
        self._record_run(memory=memory, prompt=prompt, response=response)
        return self._return_result(
            text=response,
            provider=provider,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
            session=session,
            mode=run_mode,
            tool_policy=tool_policy,
            plan=plan,
            channel=channel,
            phase=phase,
            approval_id=approval_id,
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            trace_root=trace_root,
        )

    def _record_run(self, *, memory: FileMemoryStore, prompt: str, response: str) -> None:
        memory.append("last_prompt", prompt)
        memory.append("last_response", response)
        logger.info(
            "运行记忆已记录 prompt_chars=%s response_chars=%s",
            len(prompt),
            len(response),
        )

    def _notify_thinking(
        self,
        *,
        channel: Channel,
        step: int,
        max_steps: int,
        phase: str,
    ) -> None:
        notify_channel(
            partial(
                channel.on_thinking,
                step=step,
                max_steps=max_steps,
                phase=phase,
            )
        )

    def _notify_done(
        self,
        *,
        channel: Channel,
        text: str,
        stop_reason: str,
        steps: int,
        max_steps: int,
    ) -> None:
        notify_channel(
            partial(
                channel.on_done,
                text=text,
                stop_reason=stop_reason,
                steps=steps,
                max_steps=max_steps,
            )
        )
