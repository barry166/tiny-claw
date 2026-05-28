"""Provider abstractions for language model integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ChatMessage, ...]
    max_steps: int = 1


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str | None = None


class ModelProvider(Protocol):
    @property
    def name(self) -> str:
        """Provider identifier used in logs and health checks."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete a model request."""
