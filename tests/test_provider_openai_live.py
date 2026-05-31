from __future__ import annotations

from pathlib import Path

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_openai_main_loop_live_smoke(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI flow test")

    workdir = Path(__file__).parent.resolve()
    hello_file = workdir / "hello.txt"
    if not hello_file.exists():
        pytest.skip(f"{hello_file} is required for live read-tool flow test")

    monkeypatch.setenv("TINY_CLAW_PROVIDER", "openai")
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_WORKDIR", str(workdir))
    monkeypatch.setenv("TINY_CLAW_MAX_TOKENS", "256")
    if settings.openai_base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", settings.openai_base_url)

    configure_logging("INFO")
    app = build_application(Settings.from_env())
    result = app.run(
        prompt=(
            "请调用工具读取一下当前工作区目录下 hello.txt 文件的内容，"
            "并用一句话向我总结它说了什么。"
        ),
        max_steps=4,
        mode=RunMode.ACT,
    )

    print(f"\n{BOLD}{GREEN}最终回复{RESET}")
    print(f"{GREEN}{result.text}{RESET}")
    print(f"{GREEN}stop_reason={result.stop_reason} steps={result.steps}/{result.max_steps}{RESET}")
