"""Small token budgeting helpers."""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil

from tiny_claw._internal.provider.base import ChatMessage


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def trim_messages_to_budget(
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int,
) -> tuple[ChatMessage, ...]:
    selected: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.content)
        if selected and used + cost > max_tokens:
            break
        selected.append(message)
        used += cost
    return tuple(reversed(selected))
