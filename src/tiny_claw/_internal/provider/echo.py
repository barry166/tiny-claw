"""Local echo provider used as the default no-dependency implementation."""

from __future__ import annotations

from dataclasses import dataclass

from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message, Role


@dataclass(frozen=True)
class EchoProvider:
    model: str = "echo"

    @property
    def name(self) -> str:
        return "echo"

    def complete(self, request: LLMRequest) -> LLMResponse:
        user_content = _last_user_content(request)
        text = user_content if user_content else "Tiny Claw is ready."
        return LLMResponse(
            message=Message(role=Role.ASSISTANT, content=text),
            provider=self.name,
            model=self.model,
        )


def _last_user_content(request: LLMRequest) -> str:
    for message in reversed(request.messages):
        if message.role == Role.USER:
            return message.content
    return ""
