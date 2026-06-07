from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.channel import Channel
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.integrations.feishu import FeishuChannel, FeishuEventAdapter
from tiny_claw._internal.integrations.feishu.bot import FeishuSdkMessageSender
from tiny_claw._internal.schema.message import Message, Role, ToolCall
from tiny_claw._internal.server import FEISHU_ADAPTER_KEY, ServerConfig, build_web_app
from tiny_claw._internal.settings import Settings


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send_text(self, text: str, *, reply: bool = False) -> None:
        self.messages.append((text, reply))


def test_feishu_channel_implements_engine_channel_and_sends_progress() -> None:
    sender = RecordingSender()
    channel: Channel = FeishuChannel(sender=sender)

    channel.on_start(prompt="hello", mode="act", max_steps=2)
    channel.on_thinking(step=1, max_steps=2, phase="act")
    channel.on_tool_call(ToolCall(id="call-1", name="read", arguments={}))
    channel.on_tool_result(
        call=ToolCall(id="call-1", name="read", arguments={}),
        result=Message(role=Role.TOOL, content="failed", metadata={"is_error": True}),
    )
    channel.on_done(text="完成", stop_reason="final", steps=2, max_steps=2)

    assert sender.messages == [
        ("收到请求，开始处理。", False),
        ("处理中：第 1/2 轮，阶段 act。", False),
        ("正在调用工具：read。", False),
        ("工具 read 失败。", False),
        ("完成", True),
    ]


def test_feishu_channel_prints_tool_error_fallback_hint() -> None:
    sender = RecordingSender()
    channel = FeishuChannel(sender=sender)

    channel.on_tool_result(
        call=ToolCall(id="call-1", name="read", arguments={}),
        result=Message(
            role=Role.TOOL,
            content="translated",
            metadata={
                "is_error": True,
                "error_type": "read_path_not_found",
                "suggested_tool": "bash",
            },
        ),
    )

    assert sender.messages == [
        ("工具 read 失败，已触发错误兜底：read_path_not_found。建议下一步：bash。", False),
    ]


def test_feishu_sdk_sender_replies_with_original_message_id() -> None:
    sdk_channel = RecordingSdkChannel()
    sender = FeishuSdkMessageSender(
        sdk_channel=sdk_channel,
        chat_id="chat-id",
        message_id="message-id",
    )

    asyncio.run(sender.send_text("hello", reply=True))

    assert sdk_channel.sent == [
        ("chat-id", {"text": "hello"}, {"reply_to": "message-id"}),
    ]


def test_feishu_event_adapter_starts_and_forwards_webhook_request(tmp_path) -> None:
    asyncio.run(_run_feishu_event_adapter_starts_and_forwards_webhook_request(tmp_path))


async def _run_feishu_event_adapter_starts_and_forwards_webhook_request(tmp_path) -> None:
    app = build_application(_echo_settings(tmp_path))
    sdk_channel = RecordingSdkChannel()
    adapter = FeishuEventAdapter(
        app=app,
        sdk_channel=sdk_channel,
        max_steps=1,
        mode=RunMode.ACT,
    )

    await adapter.start()
    status, body = await adapter.handle_webhook_request(
        headers={"X-Test": "1"},
        body=b"payload",
    )

    assert sdk_channel.connected is True
    assert sdk_channel.handlers
    assert status == 202
    assert body == b"ok"
    assert sdk_channel.webhooks == [({"X-Test": "1"}, b"payload")]


def test_feishu_event_adapter_dispatches_background_run(tmp_path) -> None:
    asyncio.run(_run_feishu_event_adapter_dispatches_background_run(tmp_path))


async def _run_feishu_event_adapter_dispatches_background_run(tmp_path) -> None:
    app = build_application(_echo_settings(tmp_path))
    sdk_channel = RecordingSdkChannel()
    adapter = FeishuEventAdapter(
        app=app,
        sdk_channel=sdk_channel,
        max_steps=1,
        mode=RunMode.ACT,
    )

    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="hello from feishu",
        )
    )
    await asyncio.sleep(0.05)

    assert sdk_channel.sent[-1] == (
        "chat-id",
        {"text": "hello from feishu"},
        {"reply_to": "message-id"},
    )


def test_feishu_event_adapter_uses_chat_session_memory(tmp_path) -> None:
    asyncio.run(_run_feishu_event_adapter_uses_chat_session_memory(tmp_path))


async def _run_feishu_event_adapter_uses_chat_session_memory(tmp_path) -> None:
    app = build_application(_echo_settings(tmp_path))
    sdk_channel = RecordingSdkChannel()
    adapter = FeishuEventAdapter(
        app=app,
        sdk_channel=sdk_channel,
        max_steps=1,
        mode=RunMode.ACT,
    )

    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="first message",
            chat_id="chat-a",
        )
    )
    await asyncio.sleep(0.05)
    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="second message",
            chat_id="chat-a",
        )
    )
    await asyncio.sleep(0.05)

    session = app.session_manager.resolve_feishu_chat("chat-a")
    assert app.session_manager.memory_store(session).read_recent(limit=4) == (
        "last_prompt: first message",
        "last_response: first message",
        "last_prompt: second message",
        "last_response: second message",
    )


def test_feishu_event_adapter_isolates_different_chat_sessions(tmp_path) -> None:
    asyncio.run(_run_feishu_event_adapter_isolates_different_chat_sessions(tmp_path))


async def _run_feishu_event_adapter_isolates_different_chat_sessions(tmp_path) -> None:
    app = build_application(_echo_settings(tmp_path))
    sdk_channel = RecordingSdkChannel()
    adapter = FeishuEventAdapter(
        app=app,
        sdk_channel=sdk_channel,
        max_steps=1,
        mode=RunMode.ACT,
    )

    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="hello a",
            chat_id="chat-a",
        )
    )
    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="hello b",
            chat_id="chat-b",
        )
    )
    await asyncio.sleep(0.05)

    session_a = app.session_manager.resolve_feishu_chat("chat-a")
    session_b = app.session_manager.resolve_feishu_chat("chat-b")
    assert app.session_manager.memory_store(session_a).read_recent(limit=2) == (
        "last_prompt: hello a",
        "last_response: hello a",
    )
    assert app.session_manager.memory_store(session_b).read_recent(limit=2) == (
        "last_prompt: hello b",
        "last_response: hello b",
    )


def test_feishu_event_adapter_replies_with_runtime_error_without_app() -> None:
    asyncio.run(_run_feishu_event_adapter_replies_with_runtime_error_without_app())


async def _run_feishu_event_adapter_replies_with_runtime_error_without_app() -> None:
    sdk_channel = RecordingSdkChannel()
    adapter = FeishuEventAdapter(
        app=None,
        sdk_channel=sdk_channel,
        max_steps=1,
        mode=RunMode.ACT,
        runtime_error="OpenAI provider 未配置，请设置 OPENAI_API_KEY。",
    )

    await adapter._on_message(
        FakeInboundMessage(
            content=FakeContent(kind="text"),
            content_text="hello from feishu",
        )
    )
    await asyncio.sleep(0.05)

    assert sdk_channel.sent == [
        (
            "chat-id",
            {"text": "OpenAI provider 未配置，请设置 OPENAI_API_KEY。"},
            {"reply_to": "message-id"},
        )
    ]


def test_server_health_and_feishu_endpoint(monkeypatch, tmp_path) -> None:
    asyncio.run(_run_server_health_and_feishu_endpoint(monkeypatch, tmp_path))


async def _run_server_health_and_feishu_endpoint(monkeypatch, tmp_path) -> None:
    app = build_application(
        Settings.from_env(
            {
                "TINY_CLAW_PROVIDER": "echo",
                "TINY_CLAW_STATE_DIR": str(tmp_path),
                "FEISHU_APP_ID": "cli_xxx",
                "FEISHU_APP_SECRET": "secret",
            }
        )
    )
    sdk_channel = RecordingSdkChannel()
    integration_app = build_application(_echo_settings(tmp_path))

    def fake_from_settings(**kwargs: Any) -> FeishuEventAdapter:
        assert kwargs["app"] is integration_app
        assert kwargs["runtime_error"] is None
        return FeishuEventAdapter(
            app=kwargs["app"],
            sdk_channel=sdk_channel,
            max_steps=kwargs["max_steps"],
            mode=kwargs["mode"],
        )

    monkeypatch.setattr(FeishuEventAdapter, "from_settings", fake_from_settings)
    monkeypatch.setattr(
        "tiny_claw._internal.server.build_integration_application",
        lambda _settings: integration_app,
    )
    web_app = build_web_app(
        app,
        ServerConfig(
            host="127.0.0.1",
            port=8000,
            feishu_event_path="/api/events/feishu",
            max_steps=1,
            mode=RunMode.ACT,
        ),
    )
    async with TestClient(TestServer(web_app)) as client:
        health = await client.get("/health")
        health_status = health.status
        health_body = await health.json()
        response = await client.post("/api/events/feishu", data=b"payload")
        response_status = response.status
        response_body = await response.read()

    assert health_status == 200
    assert health_body == {"status": "ok", "integrations": {"feishu": "configured"}}
    assert response_status == 202
    assert response_body == b"ok"
    assert len(sdk_channel.webhooks) == 1
    headers, body = sdk_channel.webhooks[0]
    assert headers["Content-Length"] == "7"
    assert body == b"payload"


def test_server_starts_with_feishu_configuration_without_openai_key(monkeypatch, tmp_path) -> None:
    asyncio.run(
        _run_server_starts_with_feishu_configuration_without_openai_key(
            monkeypatch,
            tmp_path,
        )
    )


async def _run_server_starts_with_feishu_configuration_without_openai_key(
    monkeypatch,
    tmp_path,
) -> None:
    app = build_application(
        Settings.from_env(
            {
                "TINY_CLAW_PROVIDER": "echo",
                "TINY_CLAW_STATE_DIR": str(tmp_path),
                "FEISHU_APP_ID": "cli_xxx",
                "FEISHU_APP_SECRET": "secret",
            }
        )
    )
    sdk_channel = RecordingSdkChannel()

    def fake_from_settings(**kwargs: Any) -> FeishuEventAdapter:
        assert kwargs["app"] is None
        assert kwargs["runtime_error"] == "OpenAI provider 未配置，请设置 OPENAI_API_KEY。"
        return FeishuEventAdapter(
            app=kwargs["app"],
            sdk_channel=sdk_channel,
            max_steps=kwargs["max_steps"],
            mode=kwargs["mode"],
            runtime_error=kwargs["runtime_error"],
        )

    monkeypatch.setattr(FeishuEventAdapter, "from_settings", fake_from_settings)
    web_app = build_web_app(
        app,
        ServerConfig(
            host="127.0.0.1",
            port=8000,
            feishu_event_path="/api/events/feishu",
            max_steps=1,
            mode=RunMode.ACT,
        ),
    )

    async with TestClient(TestServer(web_app)) as client:
        health = await client.get("/health")
        health_status = health.status
        health_body = await health.json()
        adapter = web_app[FEISHU_ADAPTER_KEY]
        await adapter._on_message(
            FakeInboundMessage(
                content=FakeContent(kind="text"),
                content_text="hello from feishu",
            )
        )
        await asyncio.sleep(0.05)

    assert health_status == 200
    assert health_body == {"status": "ok", "integrations": {"feishu": "configured"}}
    assert sdk_channel.sent == [
        (
            "chat-id",
            {"text": "OpenAI provider 未配置，请设置 OPENAI_API_KEY。"},
            {"reply_to": "message-id"},
        )
    ]


def test_server_starts_without_feishu_configuration(tmp_path) -> None:
    asyncio.run(_run_server_starts_without_feishu_configuration(tmp_path))


async def _run_server_starts_without_feishu_configuration(tmp_path) -> None:
    app = build_application(_echo_settings(tmp_path))
    web_app = build_web_app(
        app,
        ServerConfig(
            host="127.0.0.1",
            port=8000,
            feishu_event_path="/api/events/feishu",
            max_steps=1,
            mode=RunMode.ACT,
        ),
    )
    async with TestClient(TestServer(web_app)) as client:
        health = await client.get("/health")
        health_status = health.status
        health_body = await health.json()
        response = await client.post("/api/events/feishu", data=b"payload")
        response_status = response.status
        response_body = await response.json()

    assert health_status == 200
    assert health_body == {"status": "ok", "integrations": {"feishu": "not_configured"}}
    assert response_status == 503
    assert response_body == {
        "error": "feishu_not_configured",
        "message": ("FEISHU_APP_ID/LARK_APP_ID and FEISHU_APP_SECRET/LARK_APP_SECRET are required"),
    }


class RecordingSdkChannel:
    def __init__(self) -> None:
        self.handlers: list[tuple[object, object | None]] = []
        self.sent: list[tuple[object, object, object]] = []
        self.webhooks: list[tuple[dict[str, str], bytes]] = []
        self.connected = False
        self.disconnected = False

    def on(self, name_or_map: object, handler: object | None = None) -> object:
        self.handlers.append((name_or_map, handler))
        return object()

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def handle_webhook_request(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        self.webhooks.append((dict(headers), body))
        return 202, b"ok"

    async def send(self, to: object, message: object, opts: object = None) -> object:
        self.sent.append((to, message, opts))
        return object()


def _echo_settings(tmp_path) -> Settings:
    return Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "echo",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )


@dataclass(frozen=True)
class FakeContent:
    kind: str


@dataclass(frozen=True)
class FakeInboundMessage:
    content: FakeContent
    content_text: str
    chat_id: str = "chat-id"
    message_id: str = "message-id"
