from __future__ import annotations

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import STOP_REASON_FINAL, RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_openai_main_loop_live_smoke(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI flow test")

    monkeypatch.setenv("TINY_CLAW_PROVIDER", "openai")
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_MAX_TOKENS", "16")
    if settings.openai_base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", settings.openai_base_url)

    configure_logging("INFO")
    app = build_application(Settings.from_env())
    result = app.run(
        prompt="Reply with exactly: tiny-claw-ok",
        max_steps=1,
        mode=RunMode.THINK,
    )

    print(f"\n{BOLD}{GREEN}最终回复{RESET}")
    print(f"{GREEN}{result.text}{RESET}")
    assert "tiny-claw-ok" in result.text.strip().lower()
    assert result.provider == "openai"
    assert result.stop_reason == STOP_REASON_FINAL
