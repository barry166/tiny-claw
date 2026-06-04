"""Unified HTTP server for external event integrations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiohttp import web

from tiny_claw._internal.app import Application, build_integration_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.integrations.feishu import FeishuEventAdapter
from tiny_claw._internal.settings import Settings

logger = logging.getLogger(__name__)
FEISHU_ADAPTER_KEY = web.AppKey("feishu_adapter", FeishuEventAdapter)
INTEGRATION_STATUS_KEY = web.AppKey("integration_status", dict[str, str])
FEISHU_NOT_CONFIGURED_MESSAGE = (
    "FEISHU_APP_ID/LARK_APP_ID and FEISHU_APP_SECRET/LARK_APP_SECRET are required"
)
INTEGRATION_RUNTIME_ERROR_MESSAGE = "OpenAI provider 未配置，请设置 OPENAI_API_KEY。"


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    feishu_event_path: str
    max_steps: int
    mode: RunMode


def config_from_settings(
    settings: Settings,
    *,
    host: str | None = None,
    port: int | None = None,
    feishu_event_path: str | None = None,
    max_steps: int = 20,
    mode: RunMode = RunMode.ACT,
) -> ServerConfig:
    return ServerConfig(
        host=host or settings.server_host,
        port=port or settings.server_port,
        feishu_event_path=feishu_event_path or settings.feishu_event_path,
        max_steps=max_steps,
        mode=mode,
    )


async def serve(app: Application, config: ServerConfig) -> None:
    web_app = build_web_app(app, config)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host=config.host, port=config.port)
    logger.info("HTTP 服务启动 host=%s port=%s", config.host, config.port)
    logger.info("飞书事件回调 endpoint=%s", config.feishu_event_path)
    await site.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def build_web_app(app: Application, config: ServerConfig) -> web.Application:
    web_app = web.Application()
    if _is_feishu_configured(app.settings):
        integration_app = _build_optional_integration_app(app.settings)
        runtime_error = None if integration_app is not None else INTEGRATION_RUNTIME_ERROR_MESSAGE
        feishu_adapter = FeishuEventAdapter.from_settings(
            app=integration_app,
            settings=app.settings,
            max_steps=config.max_steps,
            mode=config.mode,
            runtime_error=runtime_error,
        )
        web_app[FEISHU_ADAPTER_KEY] = feishu_adapter
        web_app[INTEGRATION_STATUS_KEY] = {"feishu": "configured"}
    else:
        web_app[INTEGRATION_STATUS_KEY] = {"feishu": "not_configured"}

    web_app.router.add_get("/health", _health)
    web_app.router.add_post(config.feishu_event_path, _feishu_events)
    web_app.on_startup.append(_start_integrations)
    web_app.on_cleanup.append(_stop_integrations)
    return web_app


async def _health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "integrations": request.app.get(INTEGRATION_STATUS_KEY, {}),
        }
    )


async def _feishu_events(request: web.Request) -> web.Response:
    adapter = request.app.get(FEISHU_ADAPTER_KEY)
    if adapter is None:
        return web.json_response(
            {
                "error": "feishu_not_configured",
                "message": FEISHU_NOT_CONFIGURED_MESSAGE,
            },
            status=503,
        )
    status, body = await adapter.handle_webhook_request(
        headers=request.headers,
        body=await request.read(),
    )
    return web.Response(status=status, body=body)


async def _start_integrations(app: web.Application) -> None:
    adapter = app.get(FEISHU_ADAPTER_KEY)
    if adapter is not None:
        await adapter.start()


async def _stop_integrations(app: web.Application) -> None:
    adapter = app.get(FEISHU_ADAPTER_KEY)
    if adapter is not None:
        await adapter.stop()


def _is_feishu_configured(settings: Settings) -> bool:
    return bool(settings.feishu_app_id and settings.feishu_app_secret)


def _build_optional_integration_app(settings: Settings) -> Application | None:
    try:
        return build_integration_application(settings)
    except ConfigurationError as exc:
        logger.warning("外部平台运行时未配置: %s", exc)
        return None
