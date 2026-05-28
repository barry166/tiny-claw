"""Prompt and context assembly."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tiny_claw._internal.schema.message import Message


@dataclass(frozen=True)
class PromptContext:
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class ContextBuilder:
    system_prompt: str = "You are Tiny Claw, a layered Python CLI framework."

    def build(self, *, prompt: str, memories: Sequence[str] = ()) -> PromptContext:
        messages = [Message.system(self.system_prompt)]
        if memories:
            messages.append(Message.system("Recent memory:\n" + "\n".join(memories)))
        messages.append(Message.user(prompt.strip()))
        return PromptContext(messages=tuple(messages))
