from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tiny_claw._internal.errors import ConfigurationError, ProviderError
from tiny_claw._internal.provider.base import LLMRequest, ToolChoice
from tiny_claw._internal.provider.openai import OpenAIProvider
from tiny_claw._internal.schema.message import (
    Message,
    ToolCall,
    ToolCallResult,
    ToolDefinition,
)


class FakeOpenAIClient:
    def __init__(self, response: Any) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.response


def test_openai_provider_maps_text_request_and_response() -> None:
    response = _completion_response(message=SimpleNamespace(content="hello", tool_calls=None))
    client = FakeOpenAIClient(response=response)
    provider = OpenAIProvider(api_key="key", model="gpt-test", max_tokens=123, client=client)

    result = provider.complete(LLMRequest(messages=(Message.user("ping"),)))

    assert result.text == "hello"
    assert result.provider == "openai"
    assert result.model == "gpt-test"
    assert client.requests[0]["model"] == "gpt-test"
    assert client.requests[0]["max_tokens"] == 123
    assert client.requests[0]["messages"] == [{"role": "user", "content": "ping"}]


def test_openai_provider_stores_base_url() -> None:
    response = _completion_response(message=SimpleNamespace(content="hello", tool_calls=None))
    provider = OpenAIProvider(
        api_key="key",
        model="gpt-test",
        base_url="https://openai.example/v1",
        client=FakeOpenAIClient(response=response),
    )

    assert provider.base_url == "https://openai.example/v1"


def test_openai_provider_maps_tools_tool_choice_and_tool_results() -> None:
    response = _completion_response(message=SimpleNamespace(content="done", tool_calls=None))
    client = FakeOpenAIClient(response=response)
    provider = OpenAIProvider(api_key="key", model="gpt-test", client=client)
    call = ToolCall(id="call-1", name="fake", arguments={"message": "ok"})
    tool_result = ToolCallResult(
        tool_call_id=call.id,
        name=call.name,
        content="observed",
    ).to_message()

    provider.complete(
        LLMRequest(
            messages=(Message.assistant(tool_calls=(call,)), tool_result),
            tools=(_tool_definition(),),
            tool_choice=ToolChoice.NONE,
        )
    )

    request = client.requests[0]
    assert request["tool_choice"] == "none"
    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fake",
                "description": "Fake tool.",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert request["messages"][0]["tool_calls"][0]["function"]["arguments"] == '{"message": "ok"}'
    assert request["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "observed",
    }


def test_openai_provider_parses_tool_calls() -> None:
    response = _completion_response(
        message=SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="fake", arguments='{"message": "ok"}'),
                )
            ],
        )
    )
    provider = OpenAIProvider(api_key="key", model="gpt-test", client=FakeOpenAIClient(response))

    result = provider.complete(LLMRequest(messages=(Message.user("ping"),)))

    assert result.text == ""
    assert result.message.tool_calls == (
        ToolCall(id="call-1", name="fake", arguments={"message": "ok"}),
    )


def test_openai_provider_rejects_invalid_tool_call_json() -> None:
    response = _completion_response(
        message=SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="fake", arguments="{bad"),
                )
            ],
        )
    )
    provider = OpenAIProvider(api_key="key", model="gpt-test", client=FakeOpenAIClient(response))

    with pytest.raises(ProviderError, match="not valid JSON"):
        provider.complete(LLMRequest(messages=(Message.user("ping"),)))


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="OPENAI"):
        OpenAIProvider(api_key=None, model="gpt-test", client=object())


def _completion_response(*, message: Any) -> Any:
    return SimpleNamespace(
        id="resp-1",
        model="gpt-test",
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )


def _tool_definition() -> ToolDefinition:
    return ToolDefinition(name="fake", description="Fake tool.", parameters={"type": "object"})
