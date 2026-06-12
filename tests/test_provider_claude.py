from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.provider.base import LLMRequest, ToolChoice
from tiny_claw._internal.provider.claude import ClaudeProvider
from tiny_claw._internal.schema.message import (
    Message,
    ToolCall,
    ToolCallResult,
    ToolDefinition,
)


class FakeClaudeClient:
    def __init__(self, response: Any) -> None:
        self.messages = SimpleNamespace(create=self.create)
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.response


def test_claude_provider_maps_text_request_and_response() -> None:
    response = _message_response(content=[{"type": "text", "text": "hello"}])
    client = FakeClaudeClient(response=response)
    provider = ClaudeProvider(api_key="key", model="claude-test", max_tokens=123, client=client)

    result = provider.complete(
        LLMRequest(
            messages=(
                Message.system("system prompt"),
                Message.user("ping"),
            )
        )
    )

    assert result.text == "hello"
    assert result.provider == "claude"
    assert result.model == "claude-test"
    assert client.requests[0]["model"] == "claude-test"
    assert client.requests[0]["max_tokens"] == 123
    assert client.requests[0]["system"] == "system prompt"
    assert client.requests[0]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "ping"}]}
    ]
    assert result.usage is None


def test_claude_provider_maps_usage() -> None:
    response = _message_response(
        content=[{"type": "text", "text": "hello"}],
        usage={
            "input_tokens": 12,
            "output_tokens": 8,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 2,
        },
    )
    provider = ClaudeProvider(
        api_key="key",
        model="claude-test",
        client=FakeClaudeClient(response=response),
    )

    result = provider.complete(LLMRequest(messages=(Message.user("ping"),)))

    assert result.usage is not None
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 8
    assert result.usage.total_tokens == 20
    assert result.usage.cache_read_input_tokens == 4
    assert result.usage.cache_creation_input_tokens == 2
    assert result.metadata is not None
    assert result.metadata["usage"] == {
        "input_tokens": 12,
        "output_tokens": 8,
        "cache_read_input_tokens": 4,
        "cache_creation_input_tokens": 2,
    }


def test_claude_provider_maps_tools_tool_choice_and_tool_results() -> None:
    response = _message_response(content=[{"type": "text", "text": "done"}])
    client = FakeClaudeClient(response=response)
    provider = ClaudeProvider(api_key="key", model="claude-test", client=client)
    first_result = ToolCallResult(
        tool_call_id="call-1",
        name="fake",
        content="observed one",
    ).to_message()
    second_result = ToolCallResult(
        tool_call_id="call-2",
        name="fake",
        content="observed two",
        is_error=True,
    ).to_message()

    provider.complete(
        LLMRequest(
            messages=(first_result, second_result),
            tools=(_tool_definition(),),
            tool_choice=ToolChoice.AUTO,
        )
    )

    request = client.requests[0]
    assert request["tool_choice"] == {"type": "auto"}
    assert request["tools"] == [
        {
            "name": "fake",
            "description": "Fake tool.",
            "input_schema": {"type": "object"},
        }
    ]
    assert request["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "observed one",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-2",
                    "content": "observed two",
                    "is_error": True,
                },
            ],
        }
    ]


def test_claude_provider_maps_assistant_tool_use_and_parses_tool_use() -> None:
    response = _message_response(
        content=[
            {"type": "text", "text": "using tool"},
            {"type": "tool_use", "id": "call-2", "name": "fake", "input": {"message": "next"}},
        ]
    )
    client = FakeClaudeClient(response=response)
    provider = ClaudeProvider(api_key="key", model="claude-test", client=client)
    call = ToolCall(id="call-1", name="fake", arguments={"message": "ok"})

    result = provider.complete(LLMRequest(messages=(Message.assistant(tool_calls=(call,)),)))

    assert client.requests[0]["messages"] == [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call-1", "name": "fake", "input": {"message": "ok"}}
            ],
        }
    ]
    assert result.text == "using tool"
    assert result.message.tool_calls == (
        ToolCall(id="call-2", name="fake", arguments={"message": "next"}),
    )


def test_claude_provider_maps_none_tool_choice() -> None:
    response = _message_response(content=[{"type": "text", "text": "hello"}])
    client = FakeClaudeClient(response=response)
    provider = ClaudeProvider(api_key="key", model="claude-test", client=client)

    provider.complete(LLMRequest(messages=(Message.user("ping"),), tool_choice=ToolChoice.NONE))

    assert client.requests[0]["tool_choice"] == {"type": "none"}


def test_claude_provider_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="CLAUDE"):
        ClaudeProvider(api_key=None, model="claude-test", client=object())


def _message_response(*, content: list[dict[str, Any]], usage: Any = None) -> Any:
    return SimpleNamespace(
        id="msg-1",
        model="claude-test",
        content=content,
        stop_reason="end_turn",
        usage=usage,
    )


def _tool_definition() -> ToolDefinition:
    return ToolDefinition(name="fake", description="Fake tool.", parameters={"type": "object"})
