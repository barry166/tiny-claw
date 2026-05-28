"""Prompt and context assembly."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tiny_claw._internal.provider.base import ChatMessage


@dataclass(frozen=True)
class PromptContext:
    messages: tuple[ChatMessage, ...]


@dataclass(frozen=True)
class ContextBuilder:
    system_prompt: str = "You are Tiny Claw, a layered Python CLI framework."

    def build(self, *, prompt: str, memories: Sequence[str] = ()) -> PromptContext:
        messages = [ChatMessage(role="system", content=self.system_prompt)]
        if memories:
            messages.append(
                ChatMessage(role="system", content="Recent memory:\n" + "\n".join(memories))
            )
        messages.append(ChatMessage(role="user", content=prompt.strip()))
        return PromptContext(messages=tuple(messages))
