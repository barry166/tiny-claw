"""Core application main loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest
from tiny_claw._internal.schema.message import Message, ToolCallResult
from tiny_claw._internal.tools.registry import ToolRegistry

STOP_REASON_FINAL = "final"
STOP_REASON_MAX_STEPS_EXHAUSTED = "max_steps_exhausted"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    text: str
    provider: str
    steps: int
    max_steps: int
    workdir: Path
    stop_reason: str


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

    def run(self, *, prompt: str, max_steps: int = 20) -> RunResult:
        if max_steps < 1:
            raise ValueError("max_steps must be greater than or equal to 1")

        recent_memory = self.memory.read_recent(limit=5)
        context = self.context_builder.build(prompt=prompt, memories=recent_memory)
        messages = list(context.messages)
        tool_definitions = self.tools.definitions()
        last_text = ""
        last_provider = self.provider.name

        logger.info(
            "主循环开始 provider=%s max_steps=%s workdir=%s tools=%s memories=%s",
            self.provider.name,
            max_steps,
            self.workdir,
            len(tool_definitions),
            len(recent_memory),
        )

        for step in range(1, max_steps + 1):
            logger.info(
                "发起模型请求 step=%s/%s messages=%s tools=%s",
                step,
                max_steps,
                len(messages),
                len(tool_definitions),
            )
            response = self.provider.complete(
                LLMRequest(
                    messages=tuple(messages),
                    tools=tool_definitions,
                    max_steps=max_steps,
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

            if not response.message.tool_calls:
                self._record_run(prompt=prompt, response=last_text)
                logger.info(
                    "主循环结束 reason=%s steps=%s/%s provider=%s",
                    STOP_REASON_FINAL,
                    step,
                    max_steps,
                    last_provider,
                )
                return RunResult(
                    text=last_text,
                    provider=last_provider,
                    steps=step,
                    max_steps=max_steps,
                    workdir=self.workdir,
                    stop_reason=STOP_REASON_FINAL,
                )

            messages.extend(self._run_tool_calls(response.message))

        self._record_run(prompt=prompt, response=last_text)
        logger.warning(
            "主循环结束 reason=%s steps=%s provider=%s",
            STOP_REASON_MAX_STEPS_EXHAUSTED,
            max_steps,
            last_provider,
        )
        return RunResult(
            text=last_text,
            provider=last_provider,
            steps=max_steps,
            max_steps=max_steps,
            workdir=self.workdir,
            stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
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
