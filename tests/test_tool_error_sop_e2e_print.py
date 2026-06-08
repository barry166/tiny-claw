from __future__ import annotations

from pathlib import Path

from tiny_claw._internal.app import build_application
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message, Role, ToolCall
from tiny_claw._internal.settings import Settings


class ScriptedProvider:
    def __init__(self, *, name: str, responses: tuple[Message, ...]) -> None:
        self._name = name
        self._responses = responses
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return LLMResponse(
            message=self._responses[index],
            provider=self.name,
            model="scripted-e2e-print",
        )


def test_print_e2e_missing_read_suggests_bash_when_visible(tmp_path) -> None:
    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    (workdir / "src").mkdir(parents=True)
    provider = ScriptedProvider(
        name="print-missing-read-with-bash",
        responses=(
            Message.assistant(
                tool_calls=(
                    ToolCall(
                        id="call-read-missing-with-bash",
                        name="read",
                        arguments={"path": "src/missing.py"},
                    ),
                )
            ),
            Message.assistant("E2E print flow done: read missing with bash visible."),
        ),
    )
    app = _build_app(
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools="read,bash",
        provider=provider,
    )

    result = app.run(prompt="Print E2E: missing read with bash visible.", max_steps=2)

    _print_run_header(
        title="E2E 1: read 缺文件 + bash 可见",
        workdir=workdir,
        state_dir=state_dir,
        enabled_tools="read,bash",
        result_text=result.text,
        stop_reason=result.stop_reason,
        steps=f"{result.steps}/{result.max_steps}",
    )
    _print_tool_observations(provider)


def test_print_e2e_missing_read_does_not_suggest_bash_when_hidden(tmp_path) -> None:
    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    (workdir / "src").mkdir(parents=True)
    provider = ScriptedProvider(
        name="print-missing-read-without-bash",
        responses=(
            Message.assistant(
                tool_calls=(
                    ToolCall(
                        id="call-read-missing-without-bash",
                        name="read",
                        arguments={"path": "src/missing.py"},
                    ),
                )
            ),
            Message.assistant("E2E print flow done: read missing without bash visible."),
        ),
    )
    app = _build_app(
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools="read",
        provider=provider,
    )

    result = app.run(prompt="Print E2E: missing read without bash visible.", max_steps=2)

    _print_run_header(
        title="E2E 2: read 缺文件 + bash 不可见",
        workdir=workdir,
        state_dir=state_dir,
        enabled_tools="read",
        result_text=result.text,
        stop_reason=result.stop_reason,
        steps=f"{result.steps}/{result.max_steps}",
    )
    _print_tool_observations(provider)


def test_print_e2e_repeated_read_failure_blocks_third_attempt(tmp_path) -> None:
    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    workdir.mkdir()
    repeated_call = ToolCall(
        id="call-read-missing-repeat",
        name="read",
        arguments={"path": "missing.txt"},
    )
    provider = ScriptedProvider(
        name="print-repeat-read-block",
        responses=(
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant("E2E print flow done: repeated read blocked."),
        ),
    )
    app = _build_app(
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools="read",
        provider=provider,
    )

    result = app.run(prompt="Print E2E: repeat the same failed read three times.", max_steps=4)

    _print_run_header(
        title="E2E 3: 同一 read 失败重复 3 次",
        workdir=workdir,
        state_dir=state_dir,
        enabled_tools="read",
        result_text=result.text,
        stop_reason=result.stop_reason,
        steps=f"{result.steps}/{result.max_steps}",
    )
    _print_tool_observations(provider)


def test_print_e2e_doom_loop_warning_corrects_repeated_read_prompt(tmp_path) -> None:
    workdir = tmp_path / "workdir"
    state_dir = tmp_path / "state"
    workdir.mkdir()
    repeated_call = ToolCall(
        id="call-read-secret-key",
        name="read",
        arguments={"path": "secret_key.txt"},
    )
    provider = ScriptedProvider(
        name="print-doom-loop-warning",
        responses=(
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant(tool_calls=(repeated_call,)),
            Message.assistant(
                "我已经收到死循环纠偏提醒：不再原样重试 read 工具。"
                "当前需要用户确认 secret_key.txt 是否存在或提供正确路径。"
            ),
        ),
    )
    app = _build_app(
        state_dir=state_dir,
        workdir=workdir,
        enabled_tools="read",
        provider=provider,
    )
    prompt = (
        "帮我读取当前目录下的 secret_key.txt。 注意：我们的文件系统现在非常不稳定，"
        "经常报 File Not Found。 如果报错了，请你【千万不要改变参数】，"
        "直接原样再次调用 read_file 尝试，直到成功或连续重试 5 次为止。"
    )

    result = app.run(prompt=prompt, max_steps=4)

    _print_run_header(
        title="E2E 4: Doom Loop 提醒纠正同参 read 重试",
        workdir=workdir,
        state_dir=state_dir,
        enabled_tools="read",
        result_text=result.text,
        stop_reason=result.stop_reason,
        steps=f"{result.steps}/{result.max_steps}",
    )
    print("\noriginal_prompt:", flush=True)
    print(prompt, flush=True)
    _print_tool_observations(provider)


def _build_app(
    *,
    state_dir: Path,
    workdir: Path,
    enabled_tools: str,
    provider: ScriptedProvider,
):
    return build_application(
        Settings.from_env(
            {
                "TINY_CLAW_STATE_DIR": str(state_dir),
                "TINY_CLAW_WORKDIR": str(workdir),
                "TINY_CLAW_ENABLED_TOOLS": enabled_tools,
            }
        ),
        provider=provider,
    )


def _print_run_header(
    *,
    title: str,
    workdir: Path,
    state_dir: Path,
    enabled_tools: str,
    result_text: str,
    stop_reason: str,
    steps: str,
) -> None:
    print(f"\n{'=' * 72}", flush=True)
    print(title, flush=True)
    print(f"workdir={workdir}", flush=True)
    print(f"state_dir={state_dir}", flush=True)
    print(f"enabled_tools={enabled_tools}", flush=True)
    print(f"final_result={result_text}", flush=True)
    print(f"stop_reason={stop_reason}", flush=True)
    print(f"steps={steps}", flush=True)
    print(f"{'=' * 72}", flush=True)


def _print_tool_observations(provider: ScriptedProvider) -> None:
    previous_tool_count = 0
    for request_index, request in enumerate(provider.requests, start=1):
        visible_tools = ", ".join(tool.name for tool in request.tools) or "none"
        tool_messages = [message for message in request.messages if message.role is Role.TOOL]
        new_tool_messages = tool_messages[previous_tool_count:]
        print(f"\n=== Provider request {request_index} ===", flush=True)
        print(f"visible_tools={visible_tools}", flush=True)
        if request.messages and request.messages[-1].role is Role.USER:
            print("last_user_message:", flush=True)
            print(request.messages[-1].content, flush=True)
        print(
            f"tool_observations_in_history={len(tool_messages)} "
            f"new_since_previous_request={len(new_tool_messages)}",
            flush=True,
        )
        for message in new_tool_messages:
            print(
                f"\n--- new tool={message.name} call_id={message.tool_call_id} ---",
                flush=True,
            )
            print("metadata:", dict(message.metadata), flush=True)
            if message.metadata.get("is_error") is True:
                error_type = message.metadata.get("error_type", "unknown")
                suggested_tool = message.metadata.get("suggested_tool", "none")
                attempt = message.metadata.get("attempt", "unknown")
                print(
                    (
                        "fallback_hint: 工具错误兜底已触发，"
                        f"error_type={error_type} attempt={attempt} "
                        f"suggested_tool={suggested_tool}"
                    ),
                    flush=True,
                )
            print("content:", flush=True)
            print(message.content, flush=True)
        previous_tool_count = len(tool_messages)
