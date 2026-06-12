from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.context import ContextBuilder, ContextCompactor
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message, Role, ToolCall
from tiny_claw._internal.session import SessionMemoryStore, SessionRef
from tiny_claw._internal.settings import Settings
from tiny_claw._internal.subagent import SubagentRunner
from tiny_claw._internal.tools.base import ToolInput
from tiny_claw._internal.tools.builtin.explore import ExplorerSubagentTool
from tiny_claw._internal.tools.builtin.read import ReadTool


class ScriptedProvider:
    def __init__(self, responses: list[Message]) -> None:
        self.requests: list[LLMRequest] = []
        self._responses = responses

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]
        return LLMResponse(message=response, provider=self.name, model="scripted-model")


def test_explorer_subagent_tool_schema_and_default_steps(tmp_path) -> None:
    provider = ScriptedProvider([Message.assistant("已确认事实：没有额外线索。")])
    runner = _runner(provider=provider, state_dir=tmp_path / "state")
    tool = ExplorerSubagentTool(runner=runner)

    result = tool.run(
        ToolInput(
            arguments={"task": "确认项目入口"},
            session=_session(tmp_path),
            workdir=tmp_path,
        )
    )

    assert tool.definition().name == "explore"
    assert tool.parameters["properties"]["max_steps"]["default"] == 6
    assert "[Explorer Subagent Report]" in result.content
    assert "已确认事实" in result.content
    assert provider.requests[0].max_steps == 6
    assert not hasattr(provider.requests[0], "context")


def test_explorer_subagent_rejects_invalid_arguments(tmp_path) -> None:
    provider = ScriptedProvider([Message.assistant("unused")])
    tool = ExplorerSubagentTool(runner=_runner(provider=provider, state_dir=tmp_path / "state"))

    with pytest.raises(Exception, match="task"):
        tool.run(ToolInput(arguments={"task": ""}, session=_session(tmp_path)))

    with pytest.raises(Exception, match="runtime session"):
        tool.run(ToolInput(arguments={"task": "inspect"}))


def test_subagent_runner_only_exposes_read_tool(tmp_path) -> None:
    (tmp_path / "hello.txt").write_text("tiny claw explorer\n", encoding="utf-8")
    read_call = ToolCall(
        id="call-read",
        name="read",
        arguments={"path": "hello.txt", "start_line": 1, "max_lines": 20},
    )
    provider = ScriptedProvider(
        [
            Message.assistant(tool_calls=(read_call,)),
            Message.assistant("已确认事实：hello.txt 包含 tiny claw explorer。"),
        ]
    )
    runner = _runner(provider=provider, state_dir=tmp_path / "state")

    result = runner.run_explorer(
        task="读取 hello.txt 并确认内容",
        parent_session=_session(tmp_path),
        max_steps=3,
    )

    assert result.stop_reason == "final"
    assert result.steps == 2
    assert provider.requests[0].tools == (ReadTool(root=tmp_path).definition(),)
    assert provider.requests[1].tools == (ReadTool(root=tmp_path).definition(),)
    assert any(
        message.role is Role.TOOL and "1: tiny claw explorer" in message.content
        for message in provider.requests[1].messages
    )


def test_subagent_runner_logs_start_and_finish(tmp_path, caplog) -> None:
    provider = ScriptedProvider([Message.assistant("已确认事实：日志测试完成。")])
    runner = _runner(provider=provider, state_dir=tmp_path / "state")
    parent_session = _session(tmp_path, key="parent-log")

    with caplog.at_level(logging.INFO, logger="tiny_claw._internal.subagent.runner"):
        result = runner.run_explorer(
            task="确认日志是否记录",
            parent_session=parent_session,
            max_steps=2,
        )

    assert "Explorer 子智能体启动" in caplog.text
    assert "Explorer 子智能体结束" in caplog.text
    assert "parent_session=parent-log" in caplog.text
    assert f"child_session={result.child_session_key}" in caplog.text
    assert "tools=read" in caplog.text
    assert "reason=final" in caplog.text


def test_parent_loop_receives_only_compact_subagent_observation(tmp_path) -> None:
    (tmp_path / "target.txt").write_text("subagent evidence\n", encoding="utf-8")
    parent_explore = ToolCall(
        id="call-explore",
        name="explore",
        arguments={"task": "调查 target.txt 的内容", "max_steps": 3},
    )
    child_read = ToolCall(
        id="call-child-read",
        name="read",
        arguments={"path": "target.txt"},
    )
    provider = ScriptedProvider(
        [
            Message.assistant(tool_calls=(parent_explore,)),
            Message.assistant(tool_calls=(child_read,)),
            Message.assistant("已确认事实：target.txt 包含 subagent evidence。"),
            Message.assistant("父循环已收到 explorer 报告。"),
        ]
    )
    app = build_application(
        Settings.from_env(
            {
                "TINY_CLAW_STATE_DIR": str(tmp_path / "state"),
                "TINY_CLAW_WORKDIR": str(tmp_path),
                "TINY_CLAW_ENABLED_TOOLS": "read,write,edit,bash,explore",
            }
        ),
        provider=provider,
    )

    result = app.run(prompt="请探索 target.txt", max_steps=2)

    assert result.text == "父循环已收到 explorer 报告。"
    parent_observation = provider.requests[3].messages[-1]
    assert parent_observation.role is Role.TOOL
    assert parent_observation.name == "explore"
    assert "[Explorer Subagent Report]" in parent_observation.content
    assert "subagent evidence" in parent_observation.content
    assert "call-child-read" not in parent_observation.content
    assert provider.requests[1].tools == (ReadTool(root=tmp_path).definition(),)


def test_subagent_memory_is_isolated_from_parent_session(tmp_path) -> None:
    state_dir = tmp_path / "state"
    parent_session = _session(tmp_path, key="parent-session")
    provider = ScriptedProvider([Message.assistant("已确认事实：隔离测试完成。")])
    runner = _runner(provider=provider, state_dir=state_dir)

    result = runner.run_explorer(
        task="执行隔离测试",
        parent_session=parent_session,
    )

    memory = SessionMemoryStore(state_dir)
    assert memory.for_session(parent_session).read_recent(limit=10) == ()
    child_entries = memory.for_session(
        SessionRef(
            key=result.child_session_key,
            source="subagent",
            external_id="child",
            workdir=tmp_path,
            display_name="child",
        )
    ).read_recent(limit=10)
    assert child_entries[0] == "last_prompt: 执行隔离测试"
    assert "last_response: [Explorer Subagent Report]" in child_entries[1]


def test_subagent_reports_not_found_on_max_steps(tmp_path) -> None:
    read_call = ToolCall(id="call-read", name="read", arguments={"path": "missing.txt"})
    provider = ScriptedProvider(
        [
            Message.assistant(tool_calls=(read_call,)),
            Message.assistant(tool_calls=(read_call,)),
        ]
    )
    runner = _runner(provider=provider, state_dir=tmp_path / "state")

    result = runner.run_explorer(
        task="寻找 missing.txt",
        parent_session=_session(tmp_path),
        max_steps=2,
    )

    assert result.stop_reason == "max_steps_exhausted"
    assert "未找到确切答案" in result.text
    assert "missing.txt" in result.text


def test_subagent_result_is_truncated(tmp_path) -> None:
    provider = ScriptedProvider([Message.assistant("A" * 5_000)])
    runner = _runner(
        provider=provider,
        state_dir=tmp_path / "state",
        max_result_chars=1_500,
    )

    result = runner.run_explorer(
        task="返回很长报告",
        parent_session=_session(tmp_path),
    )

    assert len(result.text) <= 1_600
    assert "Explorer report truncated" in result.text


def _runner(
    *,
    provider: ScriptedProvider,
    state_dir: Path,
    max_result_chars: int = 4_000,
) -> SubagentRunner:
    return SubagentRunner(
        provider=provider,
        context_builder=ContextBuilder(),
        context_compactor=ContextCompactor(),
        memory=SessionMemoryStore(state_dir),
        max_result_chars=max_result_chars,
    )


def _session(
    workdir: Path,
    *,
    key: str = "parent",
) -> SessionRef:
    return SessionRef(
        key=key,
        source="test",
        external_id=key,
        workdir=workdir.resolve(),
        display_name=key,
    )
