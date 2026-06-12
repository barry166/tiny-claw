"""Synchronous read-only explorer subagent runner."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from tiny_claw._internal.context import ContextBuilder, ContextCompactor
from tiny_claw._internal.engine.observations import append_tool_observations
from tiny_claw._internal.engine.run_types import (
    STOP_REASON_FINAL,
    STOP_REASON_MAX_STEPS_EXHAUSTED,
)
from tiny_claw._internal.engine.tool_executor import ToolExecutor
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest, LLMResponse, ToolChoice
from tiny_claw._internal.provider.tracking import (
    ModelCallScope,
    clear_run_summary,
    model_call_scope,
)
from tiny_claw._internal.schema.message import Message, ToolDefinition
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.registry import ToolRegistry
from tiny_claw._internal.tracing import NullTracer, SpanHandle, Tracer

SUBAGENT_DEFAULT_MAX_STEPS = 6
SUBAGENT_MAX_STEPS_LIMIT = 12
SUBAGENT_RESULT_MAX_CHARS = 4_000

logger = logging.getLogger(__name__)

EXPLORER_SUBAGENT_PROMPT = """你是一个专门负责深度探索的探路者 (Explorer Subagent)。
你的任务是根据主架构师的指令，在当前工作区内仔细阅读代码、查阅日志，搜集足够的信息。

核心纪律:
1. 你必须、且只能依靠内置工具去寻找答案。绝对不允许凭空捏造或猜测。
2. 如果你没有找到确切的答案，你必须继续使用工具深入搜索。
3. 当且仅当你找到了确切的线索后，停止调用工具，直接输出一段纯文本作为终极汇报。
4. 如果达到步数限制仍未找到确切答案，必须明确说“未找到确切答案”，并列出已检查的路径或线索。

输出要求:
- 只输出极度精炼的纯文本报告。
- 报告必须区分“已确认事实”和“未确认/未找到”。
- 不要输出完整工具日志，不要编造不存在的文件或结论。
"""


@dataclass(frozen=True)
class SubagentResult:
    text: str
    provider: str
    steps: int
    max_steps: int
    stop_reason: str
    child_session_key: str


@dataclass(frozen=True)
class SubagentRunner:
    provider: LLMProvider
    context_builder: ContextBuilder
    context_compactor: ContextCompactor
    memory: SessionMemoryStore
    tracer: Tracer = NullTracer()
    max_result_chars: int = SUBAGENT_RESULT_MAX_CHARS

    def run_explorer(
        self,
        *,
        task: str,
        parent_session: SessionRef,
        max_steps: int = SUBAGENT_DEFAULT_MAX_STEPS,
    ) -> SubagentResult:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("explore task must not be empty")

        resolved_max_steps = _bounded_max_steps(max_steps)
        child_session = _child_session(parent_session)
        trace_root = None
        if self.tracer.has_active_trace():
            trace_root = self.tracer.begin_span(
                kind="subagent.run",
                name="subagent.explorer",
                attributes={
                    "parent_session_key": parent_session.key,
                    "child_session_key": child_session.key,
                    "child_session_source": child_session.source,
                    "max_steps": resolved_max_steps,
                    "task_chars": len(normalized_task),
                },
            )
        else:
            trace_root = self.tracer.begin_trace(
                trace_id=uuid.uuid4().hex,
                session_key=child_session.key,
                session_source=child_session.source,
                kind="subagent.run",
                name="subagent.explorer",
                attributes={
                    "parent_session_key": parent_session.key,
                    "child_session_key": child_session.key,
                    "child_session_source": child_session.source,
                    "max_steps": resolved_max_steps,
                    "task_chars": len(normalized_task),
                },
            )
        logger.info(
            (
                "[Subagent] Explorer 子智能体启动 parent_session=%s "
                "child_session=%s max_steps=%s task_chars=%s workdir=%s tools=read"
            ),
            parent_session.key,
            child_session.key,
            resolved_max_steps,
            len(normalized_task),
            child_session.workdir,
        )
        child_memory = self.memory.for_session(child_session)
        recent_memory = child_memory.read_recent(limit=3)
        prompt = _render_explorer_task(normalized_task)
        context = self.context_builder.build(
            prompt=prompt,
            memories=recent_memory,
            workdir=child_session.workdir,
        )
        messages = [Message.system(EXPLORER_SUBAGENT_PROMPT.strip()), *context.messages]
        tools = _build_read_only_tools(child_session)
        tool_definitions = tools.definitions()
        tool_executor = ToolExecutor(
            tools=tools,
            tracer=self.tracer,
            visible_tool_names=tuple(definition.name for definition in tool_definitions),
        )
        last_text = ""
        last_provider = self.provider.name
        recent_observation_texts: list[str] = []
        run_id = uuid.uuid4().hex

        try:
            for step in range(1, resolved_max_steps + 1):
                compaction = self.context_compactor.compact(messages)
                response = self._complete_provider(
                    messages=compaction.messages,
                    tools=tool_definitions,
                    max_steps=resolved_max_steps,
                    session=child_session,
                    run_id=run_id,
                    step=step,
                )
                messages.append(response.message)
                last_text = response.text
                last_provider = response.provider

                if not response.message.tool_calls:
                    text = self._final_text(last_text, child_session=child_session)
                    self._record_run(memory=child_memory, prompt=normalized_task, response=text)
                    logger.info(
                        (
                            "[Subagent] Explorer 子智能体结束 child_session=%s "
                            "reason=%s steps=%s/%s provider=%s report_chars=%s"
                        ),
                        child_session.key,
                        STOP_REASON_FINAL,
                        step,
                        resolved_max_steps,
                        last_provider,
                        len(text),
                    )
                    trace_root.set_attributes(
                        {
                            "stop_reason": STOP_REASON_FINAL,
                            "steps": step,
                            "provider": last_provider,
                            "result_text_chars": len(text),
                        }
                    )
                    self._end_trace_root(trace_root)
                    return SubagentResult(
                        text=text,
                        provider=last_provider,
                        steps=step,
                        max_steps=resolved_max_steps,
                        stop_reason=STOP_REASON_FINAL,
                        child_session_key=child_session.key,
                    )

                observations = tool_executor.run_tool_calls(
                    response.message.tool_calls,
                    session=child_session,
                    workdir=child_session.workdir,
                )
                recent_observation_texts.extend(_observation_texts(observations))
                recent_observation_texts = recent_observation_texts[-3:]
                append_tool_observations(messages, observations)

            exhausted_text = self._max_steps_text(
                last_text,
                child_session=child_session,
                max_steps=resolved_max_steps,
                recent_observation_texts=tuple(recent_observation_texts),
            )
            self._record_run(
                memory=child_memory,
                prompt=normalized_task,
                response=exhausted_text,
            )
            logger.info(
                (
                    "[Subagent] Explorer 子智能体结束 child_session=%s "
                    "reason=%s steps=%s/%s provider=%s report_chars=%s"
                ),
                child_session.key,
                STOP_REASON_MAX_STEPS_EXHAUSTED,
                resolved_max_steps,
                resolved_max_steps,
                last_provider,
                len(exhausted_text),
            )
            trace_root.set_attributes(
                {
                    "stop_reason": STOP_REASON_MAX_STEPS_EXHAUSTED,
                    "steps": resolved_max_steps,
                    "provider": last_provider,
                    "result_text_chars": len(exhausted_text),
                }
            )
            self._end_trace_root(trace_root)
            return SubagentResult(
                text=exhausted_text,
                provider=last_provider,
                steps=resolved_max_steps,
                max_steps=resolved_max_steps,
                stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
                child_session_key=child_session.key,
            )
        finally:
            self._end_trace_root(trace_root)
            clear_run_summary(run_id)

    def _end_trace_root(self, trace_root: SpanHandle) -> None:
        if not trace_root.active:
            return
        if trace_root.span_id == self.tracer.root_span_id():
            self.tracer.end_trace()
        else:
            self.tracer.end_span(trace_root)

    def _final_text(self, text: str, *, child_session: SessionRef) -> str:
        body = text.strip() or "未找到确切答案。子智能体没有返回可用报告。"
        return _truncate_result(
            _render_result(
                body=body,
                child_session=child_session,
                stop_reason=STOP_REASON_FINAL,
            ),
            max_chars=self.max_result_chars,
        )

    def _max_steps_text(
        self,
        text: str,
        *,
        child_session: SessionRef,
        max_steps: int,
        recent_observation_texts: tuple[str, ...] = (),
    ) -> str:
        body = text.strip()
        if body:
            body = f"未找到确切答案。子智能体已达到步数限制 {max_steps}。\n\n最后报告:\n{body}"
        else:
            body = f"未找到确切答案。子智能体已达到步数限制 {max_steps}，且没有返回最终报告。"
        if recent_observation_texts:
            body += "\n\n最近工具线索:\n" + "\n\n".join(recent_observation_texts)
        return _truncate_result(
            _render_result(
                body=body,
                child_session=child_session,
                stop_reason=STOP_REASON_MAX_STEPS_EXHAUSTED,
            ),
            max_chars=self.max_result_chars,
        )

    def _record_run(self, *, memory: FileMemoryStore, prompt: str, response: str) -> None:
        memory.append("last_prompt", prompt)
        memory.append("last_response", response)

    def _complete_provider(
        self,
        *,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
        max_steps: int,
        session: SessionRef,
        run_id: str,
        step: int,
    ) -> LLMResponse:
        scope = ModelCallScope(
            session_key=session.key,
            session_source=session.source,
            session_display_name=session.display_name,
            run_id=run_id,
            mode="explore",
            phase="act",
            step=step,
            max_steps=max_steps,
            caller="subagent",
        )
        with model_call_scope(scope):
            try:
                return self.provider.complete(
                    LLMRequest(
                        messages=messages,
                        tools=tools,
                        max_steps=max_steps,
                        tool_choice=ToolChoice.AUTO,
                    )
                )
            except Exception:
                clear_run_summary(run_id)
                raise


def _build_read_only_tools(session: SessionRef) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadTool(root=session.workdir))
    return registry


def _bounded_max_steps(value: int) -> int:
    if value < 1:
        raise ValueError("max_steps must be greater than or equal to 1")
    return min(value, SUBAGENT_MAX_STEPS_LIMIT)


def _child_session(parent: SessionRef) -> SessionRef:
    child_id = uuid.uuid4().hex[:12]
    key = f"parent-{parent.key}-explore-{child_id}"
    return SessionRef(
        key=key,
        source="subagent",
        external_id=f"{parent.key}:explore:{child_id}",
        workdir=parent.workdir,
        display_name=f"explore:{parent.display_name}:{child_id}",
    )


def _render_explorer_task(task: str) -> str:
    return (
        "主架构师派发了一个深度探索任务。\n\n"
        f"任务:\n{task}\n\n"
        "请使用可见工具查找证据。完成后只输出极度精炼的纯文本报告。"
    )


def _render_result(*, child_session: SessionRef, stop_reason: str, body: str) -> str:
    return (
        "[Explorer Subagent Report]\n"
        f"child_session={child_session.key}\n"
        f"stop_reason={stop_reason}\n\n"
        f"{body}"
    )


def _observation_texts(observations: tuple[Message, ...]) -> tuple[str, ...]:
    texts = []
    for observation in observations:
        tool_name = observation.name or "unknown"
        content = observation.content.strip()
        if len(content) > 800:
            content = content[:800] + "\n...[tool observation truncated]..."
        texts.append(f"tool={tool_name}\n{content}")
    return tuple(texts)


def _truncate_result(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = f"\n\n...[Explorer report truncated, original length {len(text)} chars]...\n\n"
    tail_chars = max(0, max_chars - len(marker) - 1_000)
    head = text[:1_000]
    tail = text[-tail_chars:] if tail_chars else ""
    return f"{head}{marker}{tail}"
