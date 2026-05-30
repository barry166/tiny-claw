"""Core application main loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest, ToolChoice
from tiny_claw._internal.schema.message import Message, ToolCallResult
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
    memory: FileMemoryStore
    tools: ToolRegistry
    workdir: Path

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def run(
        self,
        *,
        prompt: str,
        max_steps: int = 20,
        mode: RunMode = RunMode.ACT,
    ) -> RunResult:
        if max_steps < 1:
            raise ValueError("max_steps must be greater than or equal to 1")

        recent_memory = self.memory.read_recent(limit=5)
        context = self.context_builder.build(prompt=prompt, memories=recent_memory)
        messages = list(context.messages)
        registered_tool_definitions = self.tools.definitions()
        last_text = ""
        last_provider = self.provider.name
        plan: str | None = None

        logger.info(
            "主循环开始 provider=%s mode=%s max_steps=%s workdir=%s registered_tools=%s "
            "memories=%s",
            self.provider.name,
            mode.value,
            max_steps,
            self.workdir,
            len(registered_tool_definitions),
            len(recent_memory),
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
            if mode is RunMode.PLAN_ACT and phase == "plan":
                logger.info("规划阶段开始 step=%s/%s visible_tools=0", step, max_steps)
            logger.info(
                "发起模型请求 step=%s/%s phase=%s messages=%s tool_choice=%s visible_tools=%s",
                step,
                max_steps,
                phase,
                len(messages),
                _to_tool_choice(tool_policy).value,
                len(request_tool_definitions),
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

            logger.info(
                "收到模型响应 step=%s provider=%s tool_calls=%s text_chars=%s",
                step,
                response.provider,
                tool_call_count,
                len(response.text),
            )

            if mode is RunMode.PLAN_ACT and phase == "plan":
                plan = last_text
                logger.info("规划阶段完成 step=%s plan_chars=%s", step, len(plan))

                if response.message.tool_calls:
                    logger.warning(
                        "规划阶段收到工具调用 tool_calls=%s，已阻止进入执行阶段",
                        tool_call_count,
                    )
                    self._record_run(prompt=prompt, response=last_text)
                    return RunResult(
                        text=last_text,
                        provider=last_provider,
                        steps=step,
                        max_steps=max_steps,
                        workdir=self.workdir,
                        stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                        mode=mode,
                        tool_policy=tool_policy,
                        plan=plan,
                    )

                if step == max_steps:
                    self._record_run(prompt=prompt, response=last_text)
                    logger.warning(
                        "主循环结束 reason=%s steps=%s provider=%s mode=%s phase=%s",
                        STOP_REASON_MAX_STEPS_EXHAUSTED,
                        max_steps,
                        last_provider,
                        mode.value,
                        phase,
                    )
                    return RunResult(
                        text=last_text,
                        provider=last_provider,
                        steps=step,
                        max_steps=max_steps,
                        workdir=self.workdir,
                        stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                        mode=mode,
                        tool_policy=tool_policy,
                        plan=plan,
                    )

                messages.append(
                    Message.system(
                        "规划阶段已完成。请基于上一条 assistant 计划进入执行阶段；"
                        "如需要，可使用已提供的工具，并在每次工具结果后继续推理。"
                    )
                )
                logger.info(
                    "进入执行阶段 next_step=%s visible_tools=%s",
                    step + 1,
                    len(registered_tool_definitions),
                )
                continue

            if not response.message.tool_calls:
                self._record_run(prompt=prompt, response=last_text)
                logger.info(
                    "主循环结束 reason=%s steps=%s/%s provider=%s mode=%s phase=%s tool_policy=%s",
                    STOP_REASON_FINAL,
                    step,
                    max_steps,
                    last_provider,
                    mode.value,
                    phase,
                    tool_policy.value,
                )
                return RunResult(
                    text=last_text,
                    provider=last_provider,
                    steps=step,
                    max_steps=max_steps,
                    workdir=self.workdir,
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
                self._record_run(prompt=prompt, response=last_text)
                return RunResult(
                    text=last_text,
                    provider=last_provider,
                    steps=step,
                    max_steps=max_steps,
                    workdir=self.workdir,
                    stop_reason=STOP_REASON_TOOL_POLICY_BLOCKED,
                    mode=mode,
                    tool_policy=tool_policy,
                    plan=plan,
                )

            messages.extend(self._run_tool_calls(response.message))

        self._record_run(prompt=prompt, response=last_text)
        logger.warning(
            "主循环结束 reason=%s steps=%s provider=%s mode=%s tool_policy=%s",
            STOP_REASON_MAX_STEPS_EXHAUSTED,
            max_steps,
            last_provider,
            mode.value,
            tool_policy.value,
        )
        return RunResult(
            text=last_text,
            provider=last_provider,
            steps=max_steps,
            max_steps=max_steps,
            workdir=self.workdir,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            mode=mode,
            tool_policy=tool_policy,
            plan=plan,
        )

    def _run_tool_calls(self, message: Message) -> tuple[Message, ...]:
        observations: list[Message] = []
        for tool_call in message.tool_calls:
            try:
                logger.info("准备调用工具 id=%s name=%s", tool_call.id, tool_call.name)
                output = self.tools.call(tool_call.name, tool_call.arguments)
                logger.info(
                    "工具调用完成 id=%s name=%s is_error=%s output_chars=%s",
                    tool_call.id,
                    tool_call.name,
                    output.is_error,
                    len(output.content),
                )
                result = ToolCallResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=output.content,
                    is_error=output.is_error,
                )
            except ToolError as exc:
                logger.warning(
                    "工具调用失败 id=%s name=%s error=%s",
                    tool_call.id,
                    tool_call.name,
                    exc,
                )
                result = ToolCallResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=str(exc),
                    is_error=True,
                )
            observations.append(result.to_message())
        return tuple(observations)

    def _record_run(self, *, prompt: str, response: str) -> None:
        self.memory.append("last_prompt", prompt)
        self.memory.append("last_response", response)
        logger.info(
            "运行记忆已记录 prompt_chars=%s response_chars=%s",
            len(prompt),
            len(response),
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
