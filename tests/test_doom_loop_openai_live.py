from __future__ import annotations

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_openai_doom_loop_warning_live_e2e(tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY or OPENAI_KEY is required for live OpenAI doom loop test")

    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    workdir.mkdir()

    configure_logging("INFO")
    prompt = (
        "帮我读取当前目录下的 secret_key.txt。 注意：我们的文件系统现在非常不稳定，"
        "经常报 File Not Found。 如果报错了，请你【千万不要改变参数】，"
        "直接原样再次调用 read_file 尝试，直到成功或连续重试 5 次为止。"
    )
    replay_command = "uv run pytest -s tests/test_doom_loop_openai_live.py"
    print(f"\n{BOLD}{GREEN}OpenAI Doom Loop live e2e 流程日志{RESET}", flush=True)
    print(f"{GREEN}workdir={workdir}{RESET}", flush=True)
    print(f"{GREEN}state_dir={state_dir}{RESET}", flush=True)
    print(f"{GREEN}enabled_tools=read{RESET}", flush=True)
    print(f"{GREEN}prompt={prompt}{RESET}", flush=True)
    print(f"{GREEN}command={replay_command}{RESET}", flush=True)

    live_settings = Settings(
        log_level=settings.log_level,
        provider_name="openai",
        model=settings.model,
        max_tokens=768,
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools=("read",),
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
    )
    app = build_application(live_settings)
    print(f"{GREEN}actual_tools={', '.join(app.health().tools)}{RESET}", flush=True)
    result = app.run(
        prompt=prompt,
        max_steps=6,
        mode=RunMode.ACT,
    )

    print(f"\n{BOLD}{GREEN}最终回复{RESET}", flush=True)
    print(f"{GREEN}{result.text}{RESET}", flush=True)
    print(
        f"{GREEN}stop_reason={result.stop_reason} steps={result.steps}/{result.max_steps}{RESET}",
        flush=True,
    )
    if result.steps < 4:
        print(
            f"{GREEN}observation=模型在 Doom Loop 提醒触发前已停止同参重试。{RESET}",
            flush=True,
        )
    else:
        print(
            f"{GREEN}observation=模型至少进入第 4 轮，请结合上方日志查看 Doom Loop 提醒。{RESET}",
            flush=True,
        )
