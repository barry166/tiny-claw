"""OpenAI Chat Completions provider adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from tiny_claw._internal.errors import ConfigurationError, ProviderError
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse, LLMUsage, ToolChoice
from tiny_claw._internal.schema.message import Message, Role, ToolCall, ToolDefinition


@dataclass(frozen=True)
class OpenAIProvider:
    api_key: str | None
    model: str
    max_tokens: int = 1024
    base_url: str | None = None
    client: Any | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ConfigurationError("OPENAI_API_KEY or OPENAI_KEY is required for openai provider")
        if self.client is None:
            object.__setattr__(self, "client", OpenAI(api_key=self.api_key, base_url=self.base_url))

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self.client
        if client is None:  # pragma: no cover - guarded by __post_init__.
            raise ProviderError("OpenAI provider client is not initialized")
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=_to_openai_messages(request.messages),
                tools=_to_openai_tools(request.tools) or None,
                tool_choice=_to_openai_tool_choice(request.tool_choice),
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # pragma: no cover - SDK exception hierarchy can change.
            raise ProviderError(f"OpenAI provider request failed: {exc}") from exc

        return _to_llm_response(response=response, provider=self.name, model=self.model)


def _to_openai_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            output.append({"role": "system", "content": message.content})
        elif message.role is Role.USER:
            output.append({"role": "user", "content": message.content})
        elif message.role is Role.ASSISTANT:
            item: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or None,
            }
            if message.tool_calls:
                item["tool_calls"] = [_to_openai_tool_call(call) for call in message.tool_calls]
            output.append(item)
        elif message.role is Role.TOOL:
            if message.tool_call_id is None:
                raise ProviderError("OpenAI tool result message requires tool_call_id")
            output.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
    return output


def _to_openai_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(dict(call.arguments)),
        },
    }


def _to_openai_tools(tools: tuple[ToolDefinition, ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            },
        }
        for tool in tools
    ]


def _to_openai_tool_choice(tool_choice: ToolChoice) -> str:
    if tool_choice is ToolChoice.NONE:
        return "none"
    return "auto"


def _to_llm_response(*, response: Any, provider: str, model: str) -> LLMResponse:
    choice = response.choices[0]
    message = choice.message
    text = message.content or ""
    tool_calls = tuple(_from_openai_tool_call(call) for call in (message.tool_calls or ()))
    raw_usage = _dump_sdk_object(getattr(response, "usage", None))
    return LLMResponse(
        message=Message.assistant(content=text, tool_calls=tool_calls),
        provider=provider,
        model=getattr(response, "model", model),
        metadata={
            "id": getattr(response, "id", None),
            "finish_reason": getattr(choice, "finish_reason", None),
            "usage": raw_usage,
        },
        usage=_to_usage(raw_usage),
    )


def _from_openai_tool_call(call: Any) -> ToolCall:
    raw_arguments = call.function.arguments or "{}"
    try:
        parsed_arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"OpenAI tool call arguments are not valid JSON: {raw_arguments}"
        ) from exc
    if not isinstance(parsed_arguments, Mapping):
        raise ProviderError("OpenAI tool call arguments must decode to an object")
    return ToolCall(
        id=call.id,
        name=call.function.name,
        arguments=parsed_arguments,
    )


def _dump_sdk_object(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _to_usage(raw_usage: Any) -> LLMUsage | None:
    if raw_usage is None:
        return None
    completion_details = _get(raw_usage, "completion_tokens_details")
    prompt_details = _get(raw_usage, "prompt_tokens_details")
    return LLMUsage(
        input_tokens=_int_or_none(_get(raw_usage, "prompt_tokens")),
        output_tokens=_int_or_none(_get(raw_usage, "completion_tokens")),
        total_tokens=_int_or_none(_get(raw_usage, "total_tokens")),
        cache_read_input_tokens=_int_or_none(_get(prompt_details, "cached_tokens")),
        reasoning_output_tokens=_int_or_none(_get(completion_details, "reasoning_tokens")),
    )


def _get(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
