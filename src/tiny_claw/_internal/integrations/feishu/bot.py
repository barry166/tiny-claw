"""Feishu bot callback skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MessageHandler(Protocol):
    def __call__(self, text: str) -> str:
        """Handle inbound text and return outbound text."""


@dataclass(frozen=True)
class FeishuMessage:
    text: str
    sender_id: str | None = None


@dataclass(frozen=True)
class FeishuBot:
    handler: MessageHandler

    def on_message(self, message: FeishuMessage) -> str:
        return self.handler(message.text)
