"""Feishu channel adapter."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from tiny_claw._internal.engine.channel import Channel
from tiny_claw._internal.schema.message import Message, ToolCall

logger = logging.getLogger(__name__)


class MessageHandler(Protocol):
    def __call__(self, text: str, *, channel: Channel | None = None) -> str:
        """Handle inbound text and return outbound text."""


class FeishuMessageSender(Protocol):
    async def send_text(
        self,
        text: str,
        *,
        reply: bool = False,
    ) -> None:
        """Send text to a Feishu conversation."""


@dataclass(frozen=True)
class FeishuMessage:
    text: str
    sender_id: str | None = None


@dataclass(frozen=True)
class FeishuChannel(Channel):
    sender: FeishuMessageSender | None = None

    def on_start(self, *, prompt: str, mode: str, max_steps: int) -> None:
        self._send("收到请求，开始处理。")

    def on_thinking(self, *, step: int, max_steps: int, phase: str) -> None:
        self._send(f"处理中：第 {step}/{max_steps} 轮，阶段 {phase}。")

    def on_tool_call(self, call: ToolCall) -> None:
        self._send(f"正在调用工具：{call.name}。")

    def on_tool_result(self, *, call: ToolCall, result: Message) -> None:
        status = "失败" if result.metadata.get("is_error") is True else "完成"
        self._send(f"工具 {call.name} {status}。")

    def on_done(self, *, text: str, stop_reason: str, steps: int, max_steps: int) -> None:
        self._send(text, reply=True)

    def _send(self, text: str, *, reply: bool = False) -> None:
        if self.sender is not None:
            _run_sender(self.sender.send_text(text, reply=reply))


@dataclass(frozen=True)
class FeishuSdkMessageSender:
    sdk_channel: FeishuSdkChannel
    chat_id: str
    message_id: str

    async def send_text(
        self,
        text: str,
        *,
        reply: bool = False,
    ) -> None:
        opts = {"reply_to": self.message_id} if reply else None
        await self.sdk_channel.send(self.chat_id, {"text": text}, opts)


@dataclass(frozen=True)
class FeishuBot:
    handler: MessageHandler

    def on_message(self, message: FeishuMessage) -> str:
        return self.handler(message.text, channel=FeishuChannel())


def _run_sender(coro: object) -> None:
    if not asyncio.iscoroutine(coro):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return

    task = loop.create_task(coro)
    task.add_done_callback(_log_sender_error)


def _log_sender_error(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - depends on platform IO failure.
        logger.warning("feishu channel send failed: %s", exc)


class FeishuSdkChannel(Protocol):
    async def send(self, to: object, message: object, opts: object = None) -> object:
        """Send a message with the Feishu SDK channel."""
