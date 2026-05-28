"""Provider contracts for language model integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tiny_claw._internal.schema.message import Message, ToolDefinition


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    max_steps: int = 1


@dataclass(frozen=True)
class LLMResponse:
    message: Message
    provider: str
    model: str | None = None

    @property
    def text(self) -> str:
        return self.message.content


class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        """Provider identifier used in logs and health checks."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Complete an LLM request."""


ChatMessage = Message
ModelRequest = LLMRequest
ModelResponse = LLMResponse
ModelProvider = LLMProvider
