from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tiny_claw._internal.session import SessionManager
from tiny_claw.cli import main


def test_run_command_exposes_mode_option(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_PROVIDER", "echo")

    exit_code = main(["run", "--mode", "think", "hello"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello"


def test_help_text_is_chinese_and_lists_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["-h"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "用法: tiny-claw" in output
    assert "命令" in output
    assert "health" in output
    assert "run" in output
    assert "serve" in output
    assert "常用环境变量" in output


def test_run_help_describes_modes_in_chinese(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "-h"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "用法: tiny-claw run" in output
    assert "模式说明" in output
    assert "act" in output
    assert "plan" in output
    assert "think" in output
    assert "plan-act" in output


def test_serve_help_describes_feishu_options_in_chinese(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["serve", "-h"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "用法: tiny-claw serve" in output
    assert "飞书事件回调路径" in output
    assert "FEISHU_APP_ID" in output


def test_run_command_accepts_plan_act_mode(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_PROVIDER", "echo")

    exit_code = main(["run", "--mode", "plan-act", "--max-steps", "1", "hello"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "# PLAN.md" in output
    assert "# TODO.md" in output


def test_run_command_accepts_plan_mode(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_PROVIDER", "echo")

    exit_code = main(["run", "--mode", "plan", "--max-steps", "1", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "# PLAN.md" in output
    assert "# TODO.md" in output


def test_health_command_returns_success(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_KEY", "test-key")

    exit_code = main(["health"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=ok" in output
    assert "provider=openai" in output


def test_run_command_uses_echo_provider(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TINY_CLAW_PROVIDER", "echo")

    exit_code = main(["run", "hello"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello"


def test_run_command_isolates_named_sessions(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TINY_CLAW_PROVIDER", "echo")

    first_exit = main(["run", "--session", "first", "hello first"])
    second_exit = main(["run", "--session", "second", "hello second"])

    capsys.readouterr()
    manager = SessionManager(state_dir=tmp_path / "state", workdir=Path.cwd())
    first = manager.resolve_cli("first")
    second = manager.resolve_cli("second")
    assert first_exit == 0
    assert second_exit == 0
    assert manager.memory_store(first).read_recent(limit=2) == (
        "last_prompt: hello first",
        "last_response: hello first",
    )
    assert manager.memory_store(second).read_recent(limit=2) == (
        "last_prompt: hello second",
        "last_response: hello second",
    )


def test_python_module_entrypoint_shows_help(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env["TINY_CLAW_STATE_DIR"] = str(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-m", "tiny_claw", "--help"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "tiny-claw" in completed.stdout


def test_serve_command_builds_unified_server_config(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    async def fake_serve(_app, config) -> None:
        captured["host"] = config.host
        captured["port"] = config.port
        captured["feishu_event_path"] = config.feishu_event_path
        captured["max_steps"] = config.max_steps
        captured["mode"] = config.mode.value

    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setattr("tiny_claw.cli.serve", fake_serve)

    exit_code = main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--feishu-path",
            "/api/events/feishu-test",
            "--max-steps",
            "3",
            "--mode",
            "think",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "host": "127.0.0.1",
        "port": 8001,
        "feishu_event_path": "/api/events/feishu-test",
        "max_steps": 3,
        "mode": "think",
    }
