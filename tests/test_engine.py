from __future__ import annotations

from pathlib import Path
from typing import Any

from tiny_claw._internal.context import ContextBuilder, ContextCompactor
from tiny_claw._internal.engine.main_loop import (
    STOP_REASON_FINAL,
    STOP_REASON_MAX_STEPS_EXHAUSTED,
    STOP_REASON_TOOL_POLICY_BLOCKED,
    MainLoop,
    RunMode,
    ToolPolicy,
)
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse, ToolChoice
from tiny_claw._internal.schema.message import Message, Role, ToolCall, ToolDefinition
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin.bash import BashTool
from tiny_claw._internal.tools.builtin.edit import EditTool
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


class LargeOutputTool:
    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "Fake tool with a large observation for compaction tests."

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
        return ToolOutput(content="BEGIN-" + ("PAYLOAD" * 80) + "-END")


def test_main_loop_accepts_injected_components(tmp_path) -> None:
    memory = SessionMemoryStore(tmp_path / "state")
    provider = FakeProvider()
    engine = _build_engine(provider=provider, memory=memory, workdir=tmp_path)
    session = _session(tmp_path)

    result = engine.run(prompt="ping", session=session)

    assert result.text == "fake response"
    assert result.provider == "fake"
    assert result.steps == 1
    assert result.max_steps == 20
    assert result.workdir == tmp_path
    assert result.stop_reason == STOP_REASON_FINAL
    assert result.mode is RunMode.ACT
    assert result.tool_policy is ToolPolicy.AUTO
    assert memory.for_session(session).read_recent(limit=2) == (
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

    engine.run(prompt="ping", max_steps=1, session=_session(tmp_path))

    assert provider.requests[0].tools == (FakeTool().definition(),)
    assert provider.requests[0].tool_choice is ToolChoice.AUTO


def test_main_loop_limits_visible_tools_for_active_skill(tmp_path) -> None:
    (tmp_path / ".claw" / "skills" / "read-only").mkdir(parents=True)
    (tmp_path / ".claw" / "skills" / "read-only" / "SKILL.md").write_text(
        """---
name: read-only
description: Read only workflow
allowed-tools: read
---

# Read Only
""",
        encoding="utf-8",
    )
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(ReadTool(root=tmp_path))
    tools.register(WriteTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    engine.run(prompt="$read-only inspect", max_steps=1, session=_session(tmp_path))

    assert provider.requests[0].tools == (ReadTool(root=tmp_path).definition(),)


def test_main_loop_keeps_global_tools_when_active_skill_omits_allowed_tools(tmp_path) -> None:
    (tmp_path / ".claw" / "skills" / "git-workflow").mkdir(parents=True)
    (tmp_path / ".claw" / "skills" / "git-workflow" / "SKILL.md").write_text(
        """---
name: git-workflow
description: Git workflow
---

# Git Workflow
""",
        encoding="utf-8",
    )
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(BashTool(workdir=tmp_path))
    tools.register(ReadTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    engine.run(prompt="$git-workflow commit", max_steps=1, session=_session(tmp_path))

    assert provider.requests[0].tools == (
        BashTool(workdir=tmp_path).definition(),
        ReadTool(root=tmp_path).definition(),
    )


def test_main_loop_hides_tools_in_think_mode(tmp_path) -> None:
    provider = FakeProvider()
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(
        prompt="analyze only",
        max_steps=1,
        mode=RunMode.THINK,
        session=_session(tmp_path),
    )

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

    result = engine.run(prompt="use tool", max_steps=2, session=_session(tmp_path))

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
        session=_session(tmp_path),
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

    result = engine.run(prompt="写入 notes.txt", max_steps=2, session=_session(tmp_path))

    assert result.text == "notes.txt 已经写入。"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-write-1"
        and message.name == "write"
        and "mode=overwrite" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_can_edit_file_then_return_summary(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    edit_call = ToolCall(
        id="call-edit-1",
        name="edit",
        arguments={"path": "notes.txt", "old_text": "beta", "new_text": "bravo"},
    )
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(edit_call,)),
            Message.assistant("notes.txt 已经局部替换。"),
        ]
    )
    tools = ToolRegistry()
    tools.register(EditTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="替换 notes.txt", max_steps=2, session=_session(tmp_path))

    assert result.text == "notes.txt 已经局部替换。"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "alpha\nbravo\ngamma\n"
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-edit-1"
        and message.name == "edit"
        and "strategy=exact" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_can_read_then_edit_existing_code_file(tmp_path) -> None:
    source = 'def greet(name: str) -> str:\n    message = f"Hello, {name}!"\n    return message\n'
    (tmp_path / "greeting.py").write_text(source, encoding="utf-8")
    read_call = ToolCall(
        id="call-read-greeting",
        name="read",
        arguments={"path": "greeting.py", "start_line": 1, "max_lines": 20},
    )
    edit_call = ToolCall(
        id="call-edit-greeting",
        name="edit",
        arguments={
            "path": "greeting.py",
            "old_text": 'message = f"Hello, {name}!"\nreturn message',
            "new_text": 'message = f"Hi, {name}!"\nreturn message.upper()',
        },
    )
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(read_call,)),
            Message.assistant(tool_calls=(edit_call,)),
            Message.assistant("greeting.py 已读取并完成局部替换。"),
        ]
    )
    tools = ToolRegistry()
    tools.register(ReadTool(root=tmp_path))
    tools.register(EditTool(root=tmp_path))
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(
        prompt="读取 greeting.py 并把问候语改成 Hi",
        max_steps=3,
        session=_session(tmp_path),
    )

    assert result.text == "greeting.py 已读取并完成局部替换。"
    assert result.stop_reason == STOP_REASON_FINAL
    assert result.steps == 3
    assert (tmp_path / "greeting.py").read_text(encoding="utf-8") == (
        'def greet(name: str) -> str:\n    message = f"Hi, {name}!"\n    return message.upper()\n'
    )
    assert provider.requests[0].tools == (
        EditTool(root=tmp_path).definition(),
        ReadTool(root=tmp_path).definition(),
    )
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-read-greeting"
        and message.name == "read"
        and '2:     message = f"Hello, {name}!"' in message.content
        for message in provider.requests[1].messages
    )
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-edit-greeting"
        and message.name == "edit"
        and "strategy=line_by_line_normalized" in message.content
        and '2:     message = f"Hi, {name}!"' in message.content
        for message in provider.requests[2].messages
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

    result = engine.run(prompt="检查 missing-file", max_steps=2, session=_session(tmp_path))

    assert result.text == "命令失败，因为文件不存在。"
    assert any(
        message.role is Role.TOOL
        and message.tool_call_id == "call-bash-1"
        and message.name == "bash"
        and message.metadata["is_error"] is True
        and "exit_code=" in message.content
        for message in provider.requests[1].messages
    )


def test_main_loop_compacts_provider_request_without_mutating_history(tmp_path, caplog) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(call,)),
            Message.assistant("done"),
        ]
    )
    tools = ToolRegistry()
    tools.register(LargeOutputTool())
    engine = _build_engine(
        provider=provider,
        tools=tools,
        workdir=tmp_path,
        context_compactor=ContextCompactor(
            max_chars=800,
            retain_last_messages=2,
            recent_tool_result_head_chars=8,
            recent_tool_result_tail_chars=8,
        ),
    )

    with caplog.at_level("INFO"):
        result = engine.run(prompt="use tool", max_steps=2, session=_session(tmp_path))

    assert result.text == "done"
    tool_message = _first_tool_message(provider.requests[1].messages)
    assert tool_message is not None
    assert "中间内容已截断" in tool_message.content
    assert "BEGIN-" in tool_message.content
    assert "-END" in tool_message.content
    assert any(message.tool_calls == (call,) for message in provider.requests[1].messages)
    assert "上下文已压缩" in caplog.text


def test_main_loop_keeps_original_history_after_compacted_request(tmp_path) -> None:
    first_call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    second_call = ToolCall(id="call-2", name="fake_tool", arguments={"message": "again"})
    provider = FakeProvider(
        responses=[
            Message.assistant(tool_calls=(first_call,)),
            Message.assistant(tool_calls=(second_call,)),
            Message.assistant("done"),
        ]
    )
    tools = ToolRegistry()
    tools.register(LargeOutputTool())
    engine = _build_engine(
        provider=provider,
        tools=tools,
        workdir=tmp_path,
        context_compactor=ContextCompactor(
            max_chars=800,
            retain_last_messages=2,
            recent_tool_result_head_chars=8,
            recent_tool_result_tail_chars=8,
        ),
    )

    result = engine.run(prompt="use tool", max_steps=3, session=_session(tmp_path))

    assert result.text == "done"
    compacted_tool_message = _first_tool_message(provider.requests[1].messages)
    third_request_tool_messages = [
        message for message in provider.requests[2].messages if message.role is Role.TOOL
    ]
    assert compacted_tool_message is not None
    assert "中间内容已截断" in compacted_tool_message.content
    assert len(third_request_tool_messages) == 2
    assert any("早期工具输出已清理" in message.content for message in third_request_tool_messages)
    assert any("中间内容已截断" in message.content for message in third_request_tool_messages)


def test_main_loop_stops_when_max_steps_exhausted(tmp_path) -> None:
    call = ToolCall(id="call-1", name="fake_tool", arguments={"message": "ok"})
    provider = FakeProvider(responses=[Message.assistant(content="thinking", tool_calls=(call,))])
    tools = ToolRegistry()
    tools.register(FakeTool())
    engine = _build_engine(provider=provider, tools=tools, workdir=tmp_path)

    result = engine.run(prompt="use tool", max_steps=1, session=_session(tmp_path))

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

    result = engine.run(
        prompt="analyze only",
        max_steps=2,
        mode=RunMode.THINK,
        session=_session(tmp_path),
    )

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

    result = engine.run(
        prompt="plan then act",
        max_steps=2,
        mode=RunMode.PLAN_ACT,
        session=_session(tmp_path),
    )

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

    result = engine.run(
        prompt="plan then act",
        max_steps=1,
        mode=RunMode.PLAN_ACT,
        session=_session(tmp_path),
    )

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

    result = engine.run(
        prompt="plan then act",
        max_steps=2,
        mode=RunMode.PLAN_ACT,
        session=_session(tmp_path),
    )

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
    memory: SessionMemoryStore | None = None,
    tools: ToolRegistry | None = None,
    context_compactor: ContextCompactor | None = None,
) -> MainLoop:
    return MainLoop(
        provider=provider,
        context_builder=ContextBuilder(),
        context_compactor=context_compactor or ContextCompactor(),
        memory=memory or SessionMemoryStore(workdir / "state"),
        tools=tools or ToolRegistry(),
    )


def _session(workdir: Path, *, name: str = "default") -> SessionRef:
    return SessionRef(
        key=f"test-{name}",
        source="test",
        external_id=name,
        workdir=workdir.resolve(),
        display_name=name,
    )


def _first_tool_message(messages: tuple[Message, ...]) -> Message | None:
    for message in messages:
        if message.role is Role.TOOL:
            return message
    return None
