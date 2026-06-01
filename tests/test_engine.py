from __future__ import annotations

from pathlib import Path
from typing import Any

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.engine.main_loop import (
    STOP_REASON_FINAL,
    STOP_REASON_MAX_STEPS_EXHAUSTED,
    STOP_REASON_TOOL_POLICY_BLOCKED,
    MainLoop,
    RunMode,
    ToolPolicy,
)
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse, ToolChoice
from tiny_claw._internal.schema.message import Message, Role, ToolCall, ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin.bash import BashTool
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.builtin.write import WriteTool
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
    assert result.mode is RunMode.ACT
    assert result.tool_policy is ToolPolicy.AUTO
    assert memory.read_recent(limit=2) == (
        "last_prompt: ping",
        "last_response: fake response",
    )
    assert provider.requests[0].tools == ()
    assert provider.requests[0].tool_choice is ToolChoice.AUTO


def test_main_loop_sends_tool_definitions(tmp_path) -> None:
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    engine.run(prompt="ping", max_steps=1)

    assert provider.requests[0].tools == (FakeTool().definition(),)
    assert provider.requests[0].tool_choice is ToolChoice.AUTO


def test_main_loop_hides_tools_in_think_mode(tmp_path) -> None:
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="analyze only", max_steps=1, mode=RunMode.THINK)

    assert result.stop_reason == STOP_REASON_FINAL
    assert result.mode is RunMode.THINK
    assert result.tool_policy is ToolPolicy.NONE
    assert provider.requests[0].tools == ()
    assert provider.requests[0].tool_choice is ToolChoice.NONE


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


def test_main_loop_can_read_file_then_return_summary(tmp_path) -> None:
    file_content = "hello.txt 说 tiny-claw 已经可以读取文件。"
    prompt = (
        "请调用工具读取一下当前工作区目录下 hello.txt 文件的内容，并用一句话向我总结它说了什么。"
    )
    (tmp_path / "hello.txt").write_text(file_content, encoding="utf-8")
    read_call = ToolCall(
        id="call-read-1",
        name="read",
        arguments={"path": "hello.txt", "start_line": 1, "max_lines": 20},
    )
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(read_call,)),
            Message.assistant("hello.txt 说 tiny-claw 已经具备读取工作区文件的能力。"),
        ]
    )
    tools = ToolRegistry()
    tools.register(ReadTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(
        prompt=prompt,
        max_steps=2,
    )

    assert result.text == "hello.txt 说 tiny-claw 已经具备读取工作区文件的能力。"
    assert result.stop_reason == STOP_REASON_FINAL
    assert result.steps == 2
    assert provider.requests[0].tools == (ReadTool(root=tmp_path).definition(),)
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-read-1"
        and message.name == "read"
        and "1: hello.txt 说 tiny-claw 已经可以读取文件。" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_can_write_file_then_return_summary(tmp_path) -> None:
    write_call = ToolCall(
        id="call-write-1",
        name="write",
        arguments={"path": "notes.txt", "content": "hello\n", "mode": "overwrite"},
    )
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(write_call,)),
            Message.assistant("notes.txt 已经写入。"),
        ]
    )
    tools = ToolRegistry()
    tools.register(WriteTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="写入 notes.txt", max_steps=2)

    assert result.text == "notes.txt 已经写入。"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-write-1"
        and message.name == "write"
        and "mode=overwrite" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_returns_bash_error_observation_for_self_correction(tmp_path) -> None:
    bash_call = ToolCall(
        id="call-bash-1",
        name="bash",
        arguments={"command": "ls missing-file"},
    )
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(bash_call,)),
            Message.assistant("命令失败，因为文件不存在。"),
        ]
    )
    tools = ToolRegistry()
    tools.register(BashTool(workdir=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="检查 missing-file", max_steps=2)

    assert result.text == "命令失败，因为文件不存在。"
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-bash-1"
        and message.name == "bash"
        and message.metadata["is_error"] is True
        and "exit_code=" in message.content
        for message in provider.requests[1].messages
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


def test_main_loop_blocks_tool_calls_in_think_mode(tmp_path) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(responses=[Message.assistant(content="thinking", tool_calls=(call,))])
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="analyze only", max_steps=2, mode=RunMode.THINK)

    assert result.text == "thinking"
    assert result.steps == 1
    assert result.stop_reason == STOP_REASON_TOOL_POLICY_BLOCKED
    assert result.mode is RunMode.THINK
    assert result.tool_policy is ToolPolicy.NONE
    assert len(provider.requests) == 1


def test_main_loop_plan_act_plans_then_exposes_tools(tmp_path) -> None:
    provider = FakeProvider(
        responses=[
            Message.assistant("plan: use fake_tool"),
            Message.assistant("done"),
        ]
    )
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="plan then act", max_steps=2, mode=RunMode.PLAN_ACT)

    assert result.text == "done"
    assert result.steps == 2
    assert result.mode is RunMode.PLAN_ACT
    assert result.tool_policy is ToolPolicy.AUTO
    assert result.plan == "plan: use fake_tool"
    assert provider.requests[0].tools == ()
    assert provider.requests[0].tool_choice is ToolChoice.NONE
    assert provider.requests[1].tools == (FakeTool().definition(),)
    assert provider.requests[1].tool_choice is ToolChoice.AUTO
    assert any(
        message.role is Role.ASSISTANT and message.content == "plan: use fake_tool"
        for message in provider.requests[1].messages
    )
    assert any(
        message.role is Role.USER and "进入执行阶段" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_plan_act_counts_planning_toward_max_steps(tmp_path) -> None:
    provider = FakeProvider(responses=[Message.assistant("plan only")])
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="plan then act", max_steps=1, mode=RunMode.PLAN_ACT)

    assert result.text == "plan only"
    assert result.steps == 1
    assert result.stop_reason == STOP_REASON_MAX_STEPS_EXHAUSTED
    assert result.mode is RunMode.PLAN_ACT
    assert result.tool_policy is ToolPolicy.NONE
    assert result.plan == "plan only"
    assert provider.requests[0].tools == ()
    assert provider.requests[0].tool_choice is ToolChoice.NONE
    assert len(provider.requests) == 1


def test_main_loop_plan_act_blocks_tool_calls_during_plan(tmp_path) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(responses=[Message.assistant(content="plan", tool_calls=(call,))])
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="plan then act", max_steps=2, mode=RunMode.PLAN_ACT)

    assert result.text == "plan"
    assert result.steps == 1
    assert result.stop_reason == STOP_REASON_TOOL_POLICY_BLOCKED
    assert result.mode is RunMode.PLAN_ACT
    assert result.tool_policy is ToolPolicy.NONE
    assert result.plan == "plan"
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
