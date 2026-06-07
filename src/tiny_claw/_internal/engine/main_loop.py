"""Core application main loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path

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
from tiny_claw._internal.engine.channel import Channel, NullChannel, notify_channel
from tiny_claw._internal.engine.tool_executor import ToolExecutor
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest, ToolChoice
from tiny_claw._internal.schema.message import Message
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.tools.registry import ToolRegistry

STOP_REASON_FINAL = "final"
STOP_REASON_MAX_STEPS_EXHAUSTED = "max_steps_exhausted"
STOP_REASON_TOOL_POLICY_BLOCKED = "tool_policy_blocked"

logger = logging.getLogger(__name__)


class ToolPolicy(StrEnum):
    NONE = "none"
    AUTO = "auto"


class RunMode(StrEnum):
    ACT = "act"
    PLAN = "plan"
    THINK = "think"
    PLAN_ACT = "plan-act"


@dataclass(frozen=True)
class RunResult:
    text: str
    provider: str
    steps: int
    max_steps: int
    workdir: Path
    stop_reason: str
    mode: RunMode = RunMode.ACT
    tool_policy: ToolPolicy = ToolPolicy.AUTO
    plan: str | None = None


@dataclass(frozen=True)
class MainLoop:
    provider: LLMProvider
    context_builder: ContextBuilder
    context_compactor: ContextCompactor
    memory: SessionMemoryStore
    tools: ToolRegistry
    _tool_executor: ToolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tool_executor", ToolExecutor(tools=self.tools))

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
                    )
                messages.append(
                    Message.user(PlanPromptBuilder.execute_current_task_prompt(snapshot))
                )

        for step in range(1, max_steps + 1):
            phase = _phase_for_step(mode=mode, step=step, plan_required=plan_required)
            tool_policy = _tool_policy_for_phase(phase)
            request_tool_definitions = (
                registered_tool_definitions if tool_policy is ToolPolicy.AUTO else ()
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
                tool_choice=_to_tool_choice(tool_policy).value,
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
            response = self.provider.complete(
                LLMRequest(
                    messages=compaction.messages,
                    tools=request_tool_definitions,
                    max_steps=max_steps,
                    tool_choice=_to_tool_choice(tool_policy),
                )
            )
            messages.append(response.message)
            last_text = response.text
            last_provider = response.provider
            tool_call_count = len(response.message.tool_calls)

            log_view.log_model_response(
                logger,
                provider=response.provider,
                tool_calls=response.message.tool_calls,
                text=response.text,
            )

            if mode is RunMode.PLAN_ACT and phase == "plan":
                if response.message.tool_calls:
                    logger.warning(
                        "规划阶段收到工具调用 tool_calls=%s，已阻止进入执行阶段",
                        tool_call_count,
                    )
                    self._record_run(
                        memory=session_memory,
                        prompt=prompt,
                        response=last_text,
                    )
                    log_view.log_run_return(
                        logger,
                        text=last_text,
                        provider=last_provider,
                        stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                    )
                    self._notify_done(
                        channel=resolved_channel,
                        text=last_text,
                        stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                        steps=step,
                        max_steps=max_steps,
                    )
                    return RunResult(
                        text=last_text,
                        provider=last_provider,
                        steps=step,
                        max_steps=max_steps,
                        workdir=session.workdir,
                        stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                        mode=mode,
                        tool_policy=tool_policy,
                        plan=plan,
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
                    self._record_run(
                        memory=session_memory,
                        prompt=prompt,
                        response=last_text,
                    )
                    logger.warning(
                        "主循环结束 reason=%s steps=%s provider=%s mode=%s phase=%s",
                        STOP_REASON_MAX_STEPS_EXHAUSTED,
                        max_steps,
                        last_provider,
                        mode.value,
                        phase,
                    )
                    log_view.log_run_complete(
                        logger,
                        provider=last_provider,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                        steps=step,
                        max_steps=max_steps,
                        mode=mode.value,
                        phase=phase,
                        tool_policy=tool_policy.value,
                    )
                    log_view.log_run_return(
                        logger,
                        text=last_text,
                        provider=last_provider,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                    )
                    self._notify_done(
                        channel=resolved_channel,
                        text=last_text,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                        steps=step,
                        max_steps=max_steps,
                    )
                    return RunResult(
                        text=last_text,
                        provider=last_provider,
                        steps=step,
                        max_steps=max_steps,
                        workdir=session.workdir,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                        mode=mode,
                        tool_policy=tool_policy,
                        plan=plan,
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
                            continue
                    elif status in {"blocked", "completed"}:
                        snapshot = plan_files.append_blocker(
                            todo=current_plan_todo,
                            reason=last_text,
                        )
                        plan = snapshot.plan_text
                self._record_run(
                    memory=session_memory,
                    prompt=prompt,
                    response=last_text,
                )
                log_view.log_run_complete(
                    logger,
                    provider=last_provider,
                    stop_reason=STOP_REASON_FINAL,
                    steps=step,
                    max_steps=max_steps,
                    mode=mode.value,
                    phase=phase,
                    tool_policy=tool_policy.value,
                )
                log_view.log_run_return(
                    logger,
                    text=last_text,
                    provider=last_provider,
                    stop_reason=STOP_REASON_FINAL,
                )
                self._notify_done(
                    channel=resolved_channel,
                    text=last_text,
                    stop_reason=STOP_REASON_FINAL,
                    steps=step,
                    max_steps=max_steps,
                )
                return RunResult(
                    text=last_text,
                    provider=last_provider,
                    steps=step,
                    max_steps=max_steps,
                    workdir=session.workdir,
                    stop_reason=STOP_REASON_FINAL,
                    mode=mode,
                    tool_policy=tool_policy,
                    plan=plan,
                )

            if tool_policy is ToolPolicy.NONE:
                logger.warning(
                    "模型在禁用工具策略下仍返回工具调用 tool_calls=%s，已阻止执行",
                    tool_call_count,
                )
                self._record_run(
                    memory=session_memory,
                    prompt=prompt,
                    response=last_text,
                )
                log_view.log_run_return(
                    logger,
                    text=last_text,
                    provider=last_provider,
                    stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                )
                self._notify_done(
                    channel=resolved_channel,
                    text=last_text,
                    stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                    steps=step,
                    max_steps=max_steps,
                )
                return RunResult(
                    text=last_text,
                    provider=last_provider,
                    steps=step,
                    max_steps=max_steps,
                    workdir=session.workdir,
                    stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                    mode=mode,
                    tool_policy=tool_policy,
                    plan=plan,
                )

            observations = self._tool_executor.run_tool_calls(
                response.message.tool_calls,
                channel=resolved_channel,
            )
            if any(message.metadata.get("is_error") is True for message in observations):
                current_step_had_tool_error = True
            messages.extend(observations)

        self._record_run(
            memory=session_memory,
            prompt=prompt,
            response=last_text,
        )
        log_view.log_run_complete(
            logger,
            provider=last_provider,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            steps=max_steps,
            max_steps=max_steps,
            mode=mode.value,
            phase=None,
            tool_policy=tool_policy.value,
        )
        log_view.log_run_return(
            logger,
            text=last_text,
            provider=last_provider,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
        )
        self._notify_done(
            channel=resolved_channel,
            text=last_text,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            steps=max_steps,
            max_steps=max_steps,
        )
        return RunResult(
            text=last_text,
            provider=last_provider,
            steps=max_steps,
            max_steps=max_steps,
            workdir=session.workdir,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            mode=mode,
            tool_policy=tool_policy,
            plan=plan,
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
    ) -> RunResult:
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
            )

        context = self.context_builder.build(
            prompt=prompt,
            memories=memory.read_recent(limit=5),
            workdir=session.workdir,
        )
        messages = list(context.messages)
        messages.append(Message.system(PlanPromptBuilder.create_system_prompt()))
        compaction = self.context_compactor.compact(messages)
        response = self.provider.complete(
            LLMRequest(
                messages=compaction.messages,
                tools=(),
                max_steps=max_steps,
                tool_choice=ToolChoice.NONE,
            )
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
        )

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
    ) -> RunResult:
        log_view.log_run_complete(
            logger,
            provider=provider,
            stop_reason=stop_reason,
            steps=steps,
            max_steps=max_steps,
            mode=mode.value,
            phase=None,
            tool_policy=tool_policy.value,
        )
        log_view.log_run_return(
            logger,
            text=text,
            provider=provider,
            stop_reason=stop_reason,
        )
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


def _to_tool_choice(policy: ToolPolicy) -> ToolChoice:
    if policy is ToolPolicy.NONE:
        return ToolChoice.NONE
    return ToolChoice.AUTO


def _phase_for_step(*, mode: RunMode, step: int, plan_required: bool = False) -> str:
    if mode is RunMode.THINK:
        return "think"
    if mode is RunMode.PLAN_ACT and plan_required and step == 1:
        return "plan"
    if mode is RunMode.PLAN_ACT:
        return "act"
    return "act"


def _tool_policy_for_phase(phase: str) -> ToolPolicy:
    if phase in {"think", "plan"}:
        return ToolPolicy.NONE
    return ToolPolicy.AUTO
