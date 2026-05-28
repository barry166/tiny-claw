"""Local echo provider used as the default no-dependency implementation."""

from __future__ import annotations

from dataclasses import dataclass

from tiny_claw._internal.provider.base import ModelRequest, ModelResponse


@dataclass(frozen=True)
class EchoProvider:
    model: str = "echo"

    @property
    def name(self) -> str:
        return "echo"

    def complete(self, request: ModelRequest) -> ModelResponse:
        user_content = _last_user_content(request)
        text = user_content if user_content else "Tiny Claw is ready."
        return ModelResponse(text=text, provider=self.name, model=self.model)


def _last_user_content(request: ModelRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return ""
