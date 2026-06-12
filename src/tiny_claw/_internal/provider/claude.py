"""Claude Messages API provider adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from tiny_claw._internal.errors import ConfigurationError, ProviderError
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse, LLMUsage, ToolChoice
from tiny_claw._internal.schema.message import Message, Role, ToolCall, ToolDefinition


@dataclass(frozen=True)
class ClaudeProvider:
    api_key: str | None
    model: str
    max_tokens: int = 1024
    client: Any | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY or CLAUDE_KEY is required for claude provider"
            )
        if self.client is None:
            object.__setattr__(self, "client", Anthropic(api_key=self.api_key))

    @property
    def name(self) -> str:
        return "claude"

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self.client
        if client is None:  # pragma: no cover - guarded by __post_init__.
            raise ProviderError("Claude provider client is not initialized")
        system, messages = _to_claude_messages(request.messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "tool_choice": _to_claude_tool_choice(request.tool_choice),
        }
        if system:
            payload["system"] = system
        tools = _to_claude_tools(request.tools)
        if tools:
            payload["tools"] = tools

        try:
            response = client.messages.create(**payload)
        except Exception as exc:  # pragma: no cover - SDK exception hierarchy can change.
            raise ProviderError(f"Claude provider request failed: {exc}") from exc

        return _to_llm_response(response=response, provider=self.name, model=self.model)


def _to_claude_messages(messages: tuple[Message, ...]) -> tuple[str | None, list[dict[str, Any]]]:
    system_messages: list[str] = []
    output: list[dict[str, Any]] = []

    for message in messages:
        if message.role is Role.SYSTEM:
            if message.content:
                system_messages.append(message.content)
            continue

        if message.role is Role.USER:
            blocks = _text_blocks(message.content)
            _append_content(output, role="user", blocks=blocks)
        elif message.role is Role.ASSISTANT:
            blocks = _text_blocks(message.content)
            blocks.extend(_to_claude_tool_use(call) for call in message.tool_calls)
            _append_content(output, role="assistant", blocks=blocks)
        elif message.role is Role.TOOL:
            if message.tool_call_id is None:
                raise ProviderError("Claude tool result message requires tool_call_id")
            _append_content(output, role="user", blocks=[_to_claude_tool_result(message)])

    system = "\n\n".join(system_messages) if system_messages else None
    return system, output


def _append_content(
    output: list[dict[str, Any]],
    *,
    role: str,
    blocks: list[dict[str, Any]],
) -> None:
    if not blocks:
        blocks = [{"type": "text", "text": ""}]
    if output and output[-1]["role"] == role:
        output[-1]["content"].extend(blocks)
        return
    output.append({"role": role, "content": blocks})


def _text_blocks(content: str) -> list[dict[str, Any]]:
    if not content:
        return []
    return [{"type": "text", "text": content}]


def _to_claude_tool_use(call: ToolCall) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": call.id,
        "name": call.name,
        "input": dict(call.arguments),
    }


def _to_claude_tool_result(message: Message) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content,
    }
    is_error = message.metadata.get("is_error")
    if isinstance(is_error, bool):
        block["is_error"] = is_error
    return block


def _to_claude_tools(tools: tuple[ToolDefinition, ...]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": dict(tool.parameters),
        }
        for tool in tools
    ]


def _to_claude_tool_choice(tool_choice: ToolChoice) -> dict[str, str]:
    if tool_choice is ToolChoice.NONE:
        return {"type": "none"}
    return {"type": "auto"}


def _to_llm_response(*, response: Any, provider: str, model: str) -> LLMResponse:
    text_blocks: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in getattr(response, "content", ()):
        block_type = _get(block, "type")
        if block_type == "text":
            text_blocks.append(str(_get(block, "text") or ""))
        elif block_type == "tool_use":
            tool_calls.append(_from_claude_tool_use(block))

    raw_usage = _dump_sdk_object(getattr(response, "usage", None))
    return LLMResponse(
        message=Message.assistant(
            content="\n".join(part for part in text_blocks if part),
            tool_calls=tuple(tool_calls),
        ),
        provider=provider,
        model=getattr(response, "model", model),
        metadata={
            "id": getattr(response, "id", None),
            "stop_reason": getattr(response, "stop_reason", None),
            "usage": raw_usage,
        },
        usage=_to_usage(raw_usage),
    )


def _from_claude_tool_use(block: Any) -> ToolCall:
    raw_input = _get(block, "input") or {}
    if not isinstance(raw_input, Mapping):
        raise ProviderError("Claude tool_use input must be an object")
    return ToolCall(
        id=str(_get(block, "id")),
        name=str(_get(block, "name")),
        arguments=raw_input,
    )


def _get(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name)


def _dump_sdk_object(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _to_usage(raw_usage: Any) -> LLMUsage | None:
    if raw_usage is None:
        return None
    input_tokens = _int_or_none(_get(raw_usage, "input_tokens"))
    output_tokens = _int_or_none(_get(raw_usage, "output_tokens"))
    total_tokens = _sum_or_none(input_tokens, output_tokens)
    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_input_tokens=_int_or_none(_get(raw_usage, "cache_read_input_tokens")),
        cache_creation_input_tokens=_int_or_none(_get(raw_usage, "cache_creation_input_tokens")),
    )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sum_or_none(first: int | None, second: int | None) -> int | None:
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)
