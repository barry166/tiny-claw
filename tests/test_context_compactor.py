from __future__ import annotations

from tiny_claw._internal.context import ContextCompactor
from tiny_claw._internal.schema.message import Message, Role, ToolCall


def test_context_compactor_returns_messages_unchanged_when_under_budget() -> None:
    messages = (
        Message.system("system"),
        Message.user("hello"),
    )
    result = ContextCompactor(max_chars=1_000).compact(messages)

    assert result.messages == messages
    assert result.changed is False
    assert result.original_chars == result.compacted_chars


def test_context_compactor_masks_old_tool_results() -> None:
    messages = (
        Message.system("system"),
        Message.user("inspect logs"),
        Message.assistant(tool_calls=(ToolCall(id="call-1", name="read", arguments={}),)),
        Message(
            role=Role.TOOL,
            content="A" * 1_000,
            tool_call_id="call-1",
            name="read",
            metadata={"is_error": False},
        ),
        Message.assistant("done"),
        Message.user("next question"),
    )

    result = ContextCompactor(max_chars=100, retain_last_messages=2).compact(messages)

    tool_result = result.messages[3]
    assert result.masked_tool_results == 1
    assert result.truncated_tool_results == 0
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.name == "read"
    assert tool_result.metadata == {"is_error": False}
    assert "早期工具输出已清理" in tool_result.content
    assert "原始长度: 1000 chars" in tool_result.content


def test_context_compactor_truncates_recent_tool_results_with_head_tail() -> None:
    content = "HEAD-" + ("M" * 100) + "-TAIL"
    messages = (
        Message.system("system"),
        Message.user("inspect logs"),
        Message.assistant(tool_calls=(ToolCall(id="call-1", name="read", arguments={}),)),
        Message(role=Role.TOOL, content=content, tool_call_id="call-1", name="read"),
        Message.user("what happened?"),
    )

    result = ContextCompactor(
        max_chars=40,
        retain_last_messages=4,
        recent_tool_result_head_chars=5,
        recent_tool_result_tail_chars=5,
    ).compact(messages)

    tool_result = result.messages[3]
    assert result.masked_tool_results == 0
    assert result.truncated_tool_results == 1
    assert tool_result.content.startswith("HEAD-")
    assert tool_result.content.endswith("-TAIL")
    assert "中间内容已截断" in tool_result.content


def test_context_compactor_preserves_system_last_user_and_assistant_tool_calls() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "log.txt"})
    messages = (
        Message.system("S" * 200),
        Message.assistant(content="assistant", tool_calls=(call,)),
        Message.user("U" * 200),
    )

    result = ContextCompactor(max_chars=10).compact(messages)

    assert result.messages[0] == messages[0]
    assert result.messages[1] == messages[1]
    assert result.messages[2] == messages[2]
    assert result.changed is False
    assert result.still_over_budget is True
