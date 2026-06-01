from __future__ import annotations

import shutil

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_openai_node_helloworld_tools_live(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI tools test")
    if shutil.which("node") is None:
        pytest.skip("node is required for live OpenAI tools test")

    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    workdir.mkdir()

    monkeypatch.setenv("TINY_CLAW_PROVIDER", "openai")
    monkeypatch.setenv("TINY_CLAW_WORKDIR", str(workdir))
    monkeypatch.setenv("TINY_CLAW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("TINY_CLAW_ENABLED_TOOLS", "read,write,bash")
    monkeypatch.setenv("TINY_CLAW_MAX_TOKENS", "768")
    if settings.openai_base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", settings.openai_base_url)

    configure_logging("INFO")
    app = build_application(Settings.from_env())
    result = app.run(
        prompt=(
            "请帮我执行以下操作：\n"
            "1. 用 bash 查看一下我当前电脑的 node 版本。\n"
            '2. 帮我写一个简单的 helloworld.js 文件，输出 "Hello, tiny-claw!"。\n'
            "3. 用 bash 编译并运行这个 node 文件，确认它能正常工作。\n"
            "最后请用简短中文总结你执行了哪些步骤以及最终输出。"
        ),
        max_steps=8,
        mode=RunMode.ACT,
    )

    helloworld = workdir / "helloworld.js"
    print(f"\n{BOLD}{GREEN}workdir{RESET}")
    print(f"{GREEN}{workdir}{RESET}")
    print(f"\n{BOLD}{GREEN}最终回复{RESET}")
    print(f"{GREEN}{result.text}{RESET}")
    print(f"{GREEN}stop_reason={result.stop_reason} steps={result.steps}/{result.max_steps}{RESET}")
    if helloworld.exists():
        print(f"\n{BOLD}{GREEN}helloworld.js{RESET}")
        print(f"{GREEN}{helloworld.read_text(encoding='utf-8')}{RESET}")
