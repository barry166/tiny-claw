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


def test_openai_explorer_subagent_live_e2e(tmp_path) -> None:
    env_settings = Settings.from_env()
    if not env_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live Explorer Subagent test")

    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    notes_dir = workdir / "notes"
    notes_dir.mkdir(parents=True)
    sentinel = "SUBAGENT_LIVE_SENTINEL_20260610"
    second_sentinel = "READ_ONLY_CHILD_CONTEXT_OK"
    (workdir / "README.md").write_text(
        "# Live Explorer Subagent Fixture\n"
        "This file proves the parent agent did not need direct read access.\n"
        f"Primary sentinel: {sentinel}\n",
        encoding="utf-8",
    )
    (notes_dir / "architecture.txt").write_text(
        "Explorer must inspect this file through the child read tool.\n"
        f"Secondary sentinel: {second_sentinel}\n",
        encoding="utf-8",
    )

    configure_logging("INFO")
    live_settings = Settings(
        log_level=env_settings.log_level,
        provider_name="openai",
        model=env_settings.model
        if env_settings.provider_name == "openai"
        else DEFAULT_OPENAI_MODEL,
        max_tokens=1200,
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools=("explore",),
        openai_api_key=env_settings.openai_api_key,
        openai_base_url=env_settings.openai_base_url,
    )
    app = build_application(live_settings)
    session = app.session_manager.resolve_cli("openai-explorer-subagent-live")
    prompt = (
        "请不要直接回答。你必须调用 explore 工具派出 Explorer Subagent，"
        "让它调查当前工作区 README.md 和 notes/architecture.txt 的内容。"
        "Explorer 返回报告后，你再用中文总结，并原样包含它找到的两个 sentinel 字符串。"
    )
    replay_command = "uv run pytest -s tests/test_subagent_openai_live.py"

    _print_header("OpenAI Explorer Subagent live e2e 流程日志")
    _print_kv("command", replay_command)
    _print_kv("workdir", str(workdir))
    _print_kv("state_dir", str(state_dir))
    _print_kv("model", live_settings.model)
    _print_kv("actual_tools", ", ".join(app.health().tools))
    _print_kv("parent_session", session.key)
    _print_kv("prompt", prompt)
    _print_file("README.md fixture", workdir / "README.md")
    _print_file("notes/architecture.txt fixture", notes_dir / "architecture.txt")

    result = app.run(
        prompt=prompt,
        max_steps=4,
        mode=RunMode.ACT,
        session=session,
    )

    _print_header("父 MainLoop 最终回复")
    print(f"{GREEN}{result.text}{RESET}", flush=True)
    _print_kv("provider", result.provider)
    _print_kv("stop_reason", result.stop_reason)
    _print_kv("steps", f"{result.steps}/{result.max_steps}")

    _print_session_memory("parent session memory", state_dir, session.key)
    _print_child_sessions(state_dir, parent_session_key=session.key)


def _print_header(label: str) -> None:
    print(f"\n{BOLD}{GREEN}{label}{RESET}", flush=True)


def _print_kv(key: str, value: str) -> None:
    print(f"{GREEN}{key}={value}{RESET}", flush=True)


def _print_file(label: str, path: Path) -> None:
    _print_header(label)
    _print_kv("path", str(path))
    print(f"{GREEN}{path.read_text(encoding='utf-8')}{RESET}", flush=True)


def _print_session_memory(label: str, state_dir: Path, session_key: str) -> None:
    _print_header(label)
    memory_path = state_dir / "sessions" / session_key / "memory.jsonl"
    _print_kv("path", str(memory_path))
    if not memory_path.exists():
        print(f"{GREEN}<missing>{RESET}", flush=True)
        return
    for line in memory_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            print(f"{GREEN}{line}{RESET}", flush=True)
            continue
        key = payload.get("key")
        value = payload.get("value")
        print(f"{GREEN}{key}: {value}{RESET}", flush=True)


def _print_child_sessions(state_dir: Path, *, parent_session_key: str) -> None:
    _print_header("child subagent sessions")
    sessions_dir = state_dir / "sessions"
    prefix = f"parent-{parent_session_key}-explore-"
    if not sessions_dir.exists():
        print(f"{GREEN}<sessions dir missing>{RESET}", flush=True)
        return

    child_dirs = sorted(path for path in sessions_dir.iterdir() if path.name.startswith(prefix))
    if not child_dirs:
        print(f"{GREEN}<no child sessions found>{RESET}", flush=True)
        return

    for child_dir in child_dirs:
        _print_kv("child_session", child_dir.name)
        memory_path = child_dir / "memory.jsonl"
        _print_kv("memory", str(memory_path))
        if memory_path.exists():
            print(f"{GREEN}{memory_path.read_text(encoding='utf-8')}{RESET}", flush=True)
        else:
            print(f"{GREEN}<memory missing>{RESET}", flush=True)
