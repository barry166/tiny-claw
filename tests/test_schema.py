from __future__ import annotations

from tiny_claw._internal.schema.message import (
    Message,
    Role,
    ToolCall,
    ToolCallResult,
    ToolDefinition,
)


def test_message_schema_represents_react_turns() -> None:
    call = ToolCall(id="call-1", name="bash", arguments={"command": "pwd"})
    assistant = Message.assistant(tool_calls=(call,))
    observation = ToolCallResult(
        tool_call_id=call.id,
        name=call.name,
        content="/tmp/project",
    ).to_message()

    assert Role.USER.value == "user"
    assert assistant.role is Role.ASSISTANT
    assert assistant.tool_calls == (call,)
    assert observation.role is Role.TOOL
    assert observation.tool_call_id == "call-1"
    assert observation.name == "bash"


def test_tool_definition_uses_json_schema_style_parameters() -> None:
    definition = ToolDefinition(
        name="bash",
        description="Run shell command.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )

    assert definition.parameters["type"] == "object"
