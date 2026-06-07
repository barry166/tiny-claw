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


def test_openai_plan_mode_session_resume_live_e2e(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI plan test")

    workdir = tmp_path / "nextjs-workdir"
    state_dir = tmp_path / "state"
    workdir.mkdir()

    monkeypatch.setenv("TINY_CLAW_PROVIDER", "openai")
    monkeypatch.setenv("TINY_CLAW_WORKDIR", str(workdir))
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("TINY_CLAW_ENABLED_TOOLS", "read")
    monkeypatch.setenv("TINY_CLAW_MAX_TOKENS", "1024")
    if settings.openai_base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", settings.openai_base_url)

    configure_logging("INFO")
    prompt = "帮我搭建一下一下nextjs的基本框架吧"
    replay_command = "uv run pytest tests/test_plan_mode_openai_live.py -s"
    print(f"\n{BOLD}{GREEN}OpenAI plan mode live e2e 流程日志{RESET}", flush=True)
    print(f"{GREEN}workdir={workdir}{RESET}", flush=True)
    print(f"{GREEN}state_dir={state_dir}{RESET}", flush=True)
    print(f"{GREEN}prompt={prompt}{RESET}", flush=True)
    print(f"{GREEN}command={replay_command}{RESET}", flush=True)

    first_app = build_application(Settings.from_env())
    first_session = first_app.session_manager.resolve_cli(None)
    plan_dir = state_dir / "sessions" / first_session.key / "plan"
    plan_path = plan_dir / "PLAN.md"
    todo_path = plan_dir / "TODO.md"
    print(f"\n{BOLD}{GREEN}首次 plan 对话{RESET}", flush=True)
    print(f"{GREEN}session_key={first_session.key}{RESET}", flush=True)
    first_result = first_app.run(
        prompt=prompt,
        max_steps=1,
        mode=RunMode.PLAN,
        session=first_session,
    )
    _print_result("first_result", first_result.text, first_result.stop_reason, first_result.steps)
    _print_file("PLAN.md after first run", plan_path)
    _print_file("TODO.md after first run", todo_path)

    print(f"\n{BOLD}{GREEN}模拟断开后重新连接{RESET}", flush=True)
    second_app = build_application(Settings.from_env())
    second_session = second_app.session_manager.resolve_cli(None)
    print(f"{GREEN}resumed_session_key={second_session.key}{RESET}", flush=True)
    second_result = second_app.run(
        prompt="继续读取之前的 plan，不要覆盖已有计划。",
        max_steps=1,
        mode=RunMode.PLAN,
        session=second_session,
    )
    _print_result(
        "second_result",
        second_result.text,
        second_result.stop_reason,
        second_result.steps,
    )
    _print_file("PLAN.md after reconnect", plan_path)
    _print_file("TODO.md after reconnect", todo_path)


def _print_result(label: str, text: str, stop_reason: str, steps: int) -> None:
    print(f"\n{BOLD}{GREEN}{label}{RESET}", flush=True)
    print(f"{GREEN}stop_reason={stop_reason} steps={steps}{RESET}", flush=True)
    print(f"{GREEN}{text}{RESET}", flush=True)


def _print_file(label: str, path: Path) -> None:
    print(f"\n{BOLD}{GREEN}{label}{RESET}", flush=True)
    print(f"{GREEN}path={path}{RESET}", flush=True)
    if path.exists():
        print(f"{GREEN}{path.read_text(encoding='utf-8')}{RESET}", flush=True)
    else:
        print(f"{GREEN}<missing>{RESET}", flush=True)
