from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import DEFAULT_OPENAI_MODEL, Settings

GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_openai_usage_summary_print_live(tmp_path) -> None:
    env_settings = Settings.from_env()
    if not env_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI usage print test")

    state_dir = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    prompt = (
        "请用一句中文回复：tiny-claw usage tracking live test ok。不要调用工具，不要输出额外解释。"
    )
    live_settings = Settings(
        log_level="INFO",
        provider_name="openai",
        model=env_settings.model
        if env_settings.provider_name == "openai"
        else DEFAULT_OPENAI_MODEL,
        max_tokens=128,
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools=(),
        openai_api_key=env_settings.openai_api_key,
        openai_base_url=env_settings.openai_base_url,
    )

    configure_logging("INFO")
    app = build_application(live_settings)
    session = app.session_manager.resolve_cli("test_observability_001")

    _print_header("OpenAI usage tracking live print test")
    _print_kv("command", "uv run pytest -s tests/test_openai_usage_print_live.py")
    _print_kv("provider", app.engine.provider_name)
    _print_kv("model", live_settings.model)
    _print_kv("session_key", session.key)
    _print_kv("session_display_name", session.display_name)
    _print_kv("state_dir", str(state_dir))
    _print_kv("prompt", prompt)
    _print_header("MainLoop run start")

    result = app.run(
        prompt=prompt,
        max_steps=1,
        mode=RunMode.THINK,
        session=session,
    )

    _print_header("MainLoop final result")
    _print_kv("provider", result.provider)
    _print_kv("stop_reason", result.stop_reason)
    _print_kv("steps", f"{result.steps}/{result.max_steps}")
    print(f"{GREEN}{result.text}{RESET}", flush=True)

    _print_usage_jsonl(state_dir / "usage" / "model-calls.jsonl")


def _print_header(label: str) -> None:
    print(f"\n{BOLD}{GREEN}{label}{RESET}", flush=True)


def _print_kv(key: str, value: str) -> None:
    print(f"{GREEN}{key}={value}{RESET}", flush=True)


def _print_usage_jsonl(path: Path) -> None:
    _print_header("usage/model-calls.jsonl")
    _print_kv("path", str(path))
    if not path.exists():
        print(f"{GREEN}<missing>{RESET}", flush=True)
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        print(
            GREEN
            + json.dumps(
                {
                    "session_key": payload.get("session_key"),
                    "caller": payload.get("caller"),
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "usage": payload.get("usage"),
                    "latency_ms": payload.get("latency_ms"),
                    "error_type": payload.get("error_type"),
                },
                ensure_ascii=False,
            )
            + RESET,
            flush=True,
        )
