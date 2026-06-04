"""Engine channel callbacks for user-facing run progress."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from tiny_claw._internal.schema.message import Message, ToolCall

logger = logging.getLogger(__name__)


class Channel(Protocol):
    def on_start(self, *, prompt: str, mode: str, max_steps: int) -> None:
        """Notify that a run started."""

    def on_thinking(self, *, step: int, max_steps: int, phase: str) -> None:
        """Notify that the engine is processing a model turn."""

    def on_tool_call(self, call: ToolCall) -> None:
        """Notify that a tool call is about to run."""

    def on_tool_result(self, *, call: ToolCall, result: Message) -> None:
        """Notify that a tool call produced an observation."""

    def on_done(self, *, text: str, stop_reason: str, steps: int, max_steps: int) -> None:
        """Notify that a run completed."""


class NullChannel:
    def on_start(self, *, prompt: str, mode: str, max_steps: int) -> None:
        pass

    def on_thinking(self, *, step: int, max_steps: int, phase: str) -> None:
        pass

    def on_tool_call(self, call: ToolCall) -> None:
        pass

    def on_tool_result(self, *, call: ToolCall, result: Message) -> None:
        pass

    def on_done(self, *, text: str, stop_reason: str, steps: int, max_steps: int) -> None:
        pass


def notify_channel(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception as exc:  # pragma: no cover - channel implementations vary by platform.
        callback_name = getattr(callback, "__name__", callback.__class__.__name__)
        logger.warning("channel callback failed callback=%s error=%s", callback_name, exc)
