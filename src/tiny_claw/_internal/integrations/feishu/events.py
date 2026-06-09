"""Feishu event adapter for the unified HTTP server."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, Protocol, cast

from tiny_claw._internal.app import Application
from tiny_claw._internal.approval import ApprovalDecision
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.integrations.feishu.bot import (
    FeishuChannel,
    FeishuSdkMessageSender,
)
from tiny_claw._internal.session import SessionRef
from tiny_claw._internal.settings import Settings

logger = logging.getLogger(__name__)
FEISHU_MESSAGE_EVENT = "message"
APPROVAL_COMMAND_PATTERN = re.compile(
    r"^/(?P<command>approve|reject)\s+(?P<approval_id>[A-Za-z0-9_-]+)(?:\s+(?P<reason>.*))?$",
    re.I,
)


class WebhookChannel(Protocol):
    def on(self, name_or_map: object, handler: object | None = None) -> object:
        """Register a Feishu event handler."""

    async def connect(self) -> None:
        """Start webhook dispatcher."""

    async def disconnect(self) -> None:
        """Stop webhook dispatcher."""

    async def handle_webhook_request(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        """Handle one Feishu webhook request."""

    async def send(self, to: object, message: object, opts: object = None) -> object:
        """Send an outbound message."""


class FeishuInboundMessage(Protocol):
    content: object
    content_text: object
    chat_id: str
    message_id: str


@dataclass(frozen=True)
class ApprovalCommand:
    decision: ApprovalDecision
    approval_id: str
    reason: str | None = None


@dataclass(frozen=True)
class FeishuEventAdapter:
    app: Application | None
    sdk_channel: WebhookChannel
    max_steps: int
    mode: RunMode
    runtime_error: str | None = None

    @classmethod
    def from_settings(
        cls,
        *,
        app: Application | None,
        settings: Settings,
        max_steps: int,
        mode: RunMode,
        runtime_error: str | None = None,
    ) -> FeishuEventAdapter:
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise ConfigurationError(
                "FEISHU_APP_ID/LARK_APP_ID and FEISHU_APP_SECRET/LARK_APP_SECRET "
                "are required for the Feishu event endpoint"
            )
        sdk_channel = _build_sdk_channel(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            verification_token=settings.feishu_verification_token,
            encrypt_key=settings.feishu_encrypt_key,
        )
        return cls(
            app=app,
            sdk_channel=sdk_channel,
            max_steps=max_steps,
            mode=mode,
            runtime_error=runtime_error,
        )

    async def start(self) -> None:
        self.sdk_channel.on(FEISHU_MESSAGE_EVENT, self._on_message)
        await self.sdk_channel.connect()

    async def stop(self) -> None:
        await self.sdk_channel.disconnect()

    async def handle_webhook_request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        return await self.sdk_channel.handle_webhook_request(headers, body)

    async def _on_message(self, message: FeishuInboundMessage) -> None:
        text = _extract_message_text(message)
        sender = FeishuSdkMessageSender(
            sdk_channel=self.sdk_channel,
            chat_id=message.chat_id,
            message_id=message.message_id,
        )
        if text is None:
            await sender.send_text("暂只支持文本消息。", reply=True)
            return

        if self.app is None:
            await sender.send_text(self.runtime_error or "外部平台运行时未配置。", reply=True)
            return

        channel = FeishuChannel(sender=sender)
        session = self.app.session_manager.resolve_feishu_chat(message.chat_id)
        approval_command = parse_approval_command(text)
        if approval_command is not None:
            resume_task = asyncio.create_task(
                asyncio.to_thread(
                    self._resume_approval_command,
                    command=approval_command,
                    session=session,
                    channel=channel,
                )
            )
            resume_task.add_done_callback(_log_background_result)
            return

        run_task = asyncio.create_task(
            asyncio.to_thread(
                self.app.run,
                prompt=text,
                max_steps=self.max_steps,
                mode=self.mode,
                session=session,
                channel=channel,
            )
        )
        run_task.add_done_callback(_log_background_result)

    def _resume_approval_command(
        self,
        *,
        command: ApprovalCommand,
        session: SessionRef,
        channel: FeishuChannel,
    ) -> None:
        if self.app is None:
            return
        result = self.app.resume_approval(
            approval_id=command.approval_id,
            decision=command.decision,
            reason=command.reason,
            session=session,
            channel=channel,
        )
        lines = [result.message]
        if result.result_text:
            lines.extend(["", result.result_text])
        channel._send("\n".join(lines), reply=True)


def _build_sdk_channel(
    *,
    app_id: str,
    app_secret: str,
    verification_token: str | None,
    encrypt_key: str | None,
) -> WebhookChannel:
    sdk_module = cast(Any, import_module("lark_oapi.channel"))
    sdk_channel_class = sdk_module.FeishuChannel
    sdk_channel = sdk_channel_class(
        app_id=app_id,
        app_secret=app_secret,
        verification_token=verification_token,
        encrypt_key=encrypt_key,
        transport="webhook",
    )
    return cast("WebhookChannel", sdk_channel)


def _extract_message_text(message: FeishuInboundMessage) -> str | None:
    kind = getattr(message.content, "kind", None)
    content_text = getattr(message, "content_text", "")
    if not isinstance(content_text, str):
        return None
    text = content_text.strip()
    if kind == "text" and text:
        return text
    return None


def parse_approval_command(text: str) -> ApprovalCommand | None:
    match = APPROVAL_COMMAND_PATTERN.match(text.strip())
    if match is None:
        return None
    command = cast(Literal["approve", "reject"], match.group("command").lower())
    decision: ApprovalDecision = "approve" if command == "approve" else "reject"
    reason = match.group("reason")
    cleaned_reason = reason.strip() if reason is not None else ""
    return ApprovalCommand(
        decision=decision,
        approval_id=match.group("approval_id"),
        reason=cleaned_reason or None,
    )


def _log_background_result(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - depends on provider/platform IO.
        logger.exception("feishu background run failed: %s", exc)
