"""Core application main loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path

from tiny_claw._internal.context.builder import ContextBuilder
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

        for step in range(1, max_steps + 1):
            phase = _phase_for_step(mode=mode, step=step)
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
            response = self.provider.complete(
                LLMRequest(
                    messages=tuple(messages),
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
                plan = last_text
                logger.info(
                    "[Plan] 规划阶段完成 step=%s plan_chars=%s",
                    step,
                    len(plan),
                )

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
                    Message.user(
                        "规划阶段已完成。请基于上一条 assistant 计划进入执行阶段；"
                        "如需要，可使用已提供的工具，并在每次工具结果后继续推理。"
                    )
                )
                logger.info(
                    "[Plan] 进入执行阶段 next_step=%s visible_tools=%s",
                    step + 1,
                    len(registered_tool_definitions),
                )
                continue

            if not response.message.tool_calls:
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

            messages.extend(
                self._tool_executor.run_tool_calls(
                    response.message.tool_calls,
                    channel=resolved_channel,
                )
            )

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


def _phase_for_step(*, mode: RunMode, step: int) -> str:
    if mode is RunMode.THINK:
        return "think"
    if mode is RunMode.PLAN_ACT and step == 1:
        return "plan"
    if mode is RunMode.PLAN_ACT:
        return "act"
    return "act"


def _tool_policy_for_phase(phase: str) -> ToolPolicy:
    if phase in {"think", "plan"}:
        return ToolPolicy.NONE
    return ToolPolicy.AUTO
