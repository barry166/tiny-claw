from __future__ import annotations

from pathlib import Path
from typing import Any

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.engine.main_loop import (
    STOP_REASON_FINAL,
    STOP_REASON_MAX_STEPS_EXHAUSTED,
    MainLoop,
)
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message, Role, ToolCall, ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self, responses: list[Message] | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self._responses = responses or [Message.assistant("fake response")]

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]
        return LLMResponse(message=response, provider=self.name, model="fake-model")


class FakeTool:
    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "Fake tool for engine tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        return ToolOutput(content=f"observed:{input.arguments['message']}")


def test_main_loop_accepts_injected_components(tmp_path) -> None:
    memory = FileMemoryStore(tmp_path / "state")
    provider = FakeProvider()
    engine = _build_engine(provider=provider, memory=memory, workdir=tmp_path)

    result = engine.run(prompt="ping")

    assert result.text == "fake response"
    assert result.provider == "fake"
    assert result.steps == 1
    assert result.max_steps == 20
    assert result.workdir == tmp_path
    assert result.stop_reason == STOP_REASON_FINAL
    assert memory.read_recent(limit=2) == (
        "last_prompt: ping",
        "last_response: fake response",
    )
    assert provider.requests[0].tools == ()


def test_main_loop_sends_tool_definitions(tmp_path) -> None:
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    engine.run(prompt="ping", max_steps=1)

    assert provider.requests[0].tools == (FakeTool().definition(),)


def test_main_loop_runs_tool_observation_then_next_turn(tmp_path) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(call,)),
            Message.assistant("done"),
        ]
    )
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="use tool", max_steps=2)

    assert result.text == "done"
    assert result.steps == 2
    assert result.stop_reason == STOP_REASON_FINAL
    second_request_messages = provider.requests[1].messages
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-1"
        and message.content == "observed:ok"
        for message in second_request_messages
    )


def test_main_loop_stops_when_max_steps_exhausted(tmp_path) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(responses=[Message.assistant(content="thinking", tool_calls=(call,))])
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="use tool", max_steps=1)

    assert result.text == "thinking"
    assert result.steps == 1
    assert result.max_steps == 1
    assert result.stop_reason == STOP_REASON_MAX_STEPS_EXHAUSTED
    assert len(provider.requests) == 1


def _build_engine(
    *,
    provider: FakeProvider,
    workdir: Path,
    memory: FileMemoryStore | None = None,
    tools: ToolRegistry | None = None,
) -> MainLoop:
    return MainLoop(
        provider=provider,
        context_builder=ContextBuilder(),
        memory=memory or FileMemoryStore(workdir / "state"),
        tools=tools or ToolRegistry(),
        workdir=workdir,
    )
