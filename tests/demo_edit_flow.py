from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from tiny_claw._internal.app import build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

PROMPT = """请真实调用工具完成这个任务：

1. 先使用 read 工具读取 greeting.py。
2. 再使用 edit 工具只替换函数体里的下面两行，不要替换 def greet(...) 这一行：

message = f"Hello, {name}!"
return message

替换为：

message = f"Hi, {name}!"
return message.upper()

注意：edit 的 old_text 可以不带缩进，但必须提供足够上下文，让它唯一匹配。
最后请用一句话总结你调用了哪些工具，以及文件是否修改成功。
"""

INITIAL_FILE = 'def greet(name: str) -> str:\n    message = f"Hello, {name}!"\n    return message\n'

EXPECTED_FILE = (
    'def greet(name: str) -> str:\n    message = f"Hi, {name}!"\n    return message.upper()\n'
)


def main() -> int:
    base_settings = Settings.from_env()
    if base_settings.provider_name == "echo":
        print("This demo needs a real provider, not the default echo provider.")
        print("")
        print("Example:")
        print(
            "  TINY_CLAW_PROVIDER=openai OPENAI_API_KEY=... uv run python tests/demo_edit_flow.py"
        )
        print(
            "  TINY_CLAW_PROVIDER=claude ANTHROPIC_API_KEY=... "
            "uv run python tests/demo_edit_flow.py"
        )
        return 2

    with TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "workdir"
        state_dir = Path(tmp) / "state"
        workdir.mkdir()

        target = workdir / "greeting.py"
        target.write_text(INITIAL_FILE, encoding="utf-8")

        os.environ["TINY_CLAW_WORKDIR"] = str(workdir)
        os.environ["TINY_CLAW_STATE_DIR"] = str(state_dir)
        os.environ["TINY_CLAW_ENABLED_TOOLS"] = "read,edit"
        os.environ.setdefault("TINY_CLAW_MAX_TOKENS", "1024")

        configure_logging("INFO")
        app = build_application(Settings.from_env())

        print("=== Provider ===")
        print(app.engine.provider_name)
        print("")

        print("=== Initial File ===")
        print(target.read_text(encoding="utf-8"))

        result = app.run(prompt=PROMPT, max_steps=6, mode=RunMode.ACT)

        print("\n=== Final Response ===")
        print(result.text)
        print(f"stop_reason={result.stop_reason}, steps={result.steps}/{result.max_steps}")

        final_file = target.read_text(encoding="utf-8")
        print("\n=== Final File ===")
        print(final_file)

        if final_file != EXPECTED_FILE:
            print("=== Expected File ===")
            print(EXPECTED_FILE)
            print("DEMO RESULT: failed; real provider did not produce the expected edit.")
            return 1

        print("DEMO RESULT: passed; real provider produced the expected edit.")

        print("=== Temporary Workdir ===")
        print(workdir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
