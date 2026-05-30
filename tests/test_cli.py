from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tiny_claw.cli import main


def test_run_command_exposes_mode_option(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))

    exit_code = main(["run", "--mode", "think", "hello"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello"


def test_run_command_accepts_plan_act_mode(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))

    exit_code = main(["run", "--mode", "plan-act", "--max-steps", "1", "hello"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello"


def test_health_command_returns_success(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))

    exit_code = main(["health"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=ok" in output
    assert "provider=echo" in output


def test_run_command_uses_echo_provider(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(tmp_path))

    exit_code = main(["run", "hello"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello"


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
