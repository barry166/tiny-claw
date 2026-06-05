from __future__ import annotations

from pathlib import Path

from tiny_claw._internal.app import build_application
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message, Role, ToolCall
from tiny_claw._internal.settings import Settings


class ReadReadmeProvider:
    def __init__(self, *, final_text: str) -> None:
        self.requests: list[LLMRequest] = []
        self.final_text = final_text

    @property
    def name(self) -> str:
        return "read-readme"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                message=Message.assistant(
                    tool_calls=(
                        ToolCall(
                            id="call-read-readme",
                            name="read",
                            arguments={
                                "path": "README.md",
                                "start_line": 1,
                                "max_lines": 20,
                            },
                        ),
                    )
                ),
                provider=self.name,
                model="fake-model",
            )
        return LLMResponse(
            message=Message.assistant(self.final_text),
            provider=self.name,
            model="fake-model",
        )


def test_e2e_sessions_isolate_two_business_workdirs(tmp_path) -> None:
    state_dir = tmp_path / "state"
    frontend_project = tmp_path / "frontend-shop"
    backend_project = tmp_path / "backend-api"
    frontend_project.mkdir()
    backend_project.mkdir()
    (frontend_project / "README.md").write_text(
        "# Frontend Shop\n\nThis project renders the checkout UI.\n",
        encoding="utf-8",
    )

    frontend_provider = ReadReadmeProvider(final_text="frontend readme loaded")
    frontend_app = _build_project_app(
        state_dir=state_dir,
        workdir=frontend_project,
        provider=frontend_provider,
    )
    frontend_result = frontend_app.run(
        prompt="Read this project's README.",
        max_steps=2,
    )

    backend_provider = ReadReadmeProvider(final_text="backend cannot load frontend readme")
    backend_app = _build_project_app(
        state_dir=state_dir,
        workdir=backend_project,
        provider=backend_provider,
    )
    backend_result = backend_app.run(
        prompt="Read this project's README.",
        max_steps=2,
    )

    frontend_session = frontend_app.session_manager.resolve_cli(None)
    backend_session = backend_app.session_manager.resolve_cli(None)
    frontend_memory = frontend_app.session_manager.memory_store(frontend_session).read_recent(
        limit=2
    )
    backend_memory = backend_app.session_manager.memory_store(backend_session).read_recent(limit=2)

    assert frontend_session.key != backend_session.key
    assert frontend_result.workdir == frontend_project.resolve()
    assert backend_result.workdir == backend_project.resolve()
    assert frontend_result.text == "frontend readme loaded"
    assert backend_result.text == "backend cannot load frontend readme"
    assert frontend_memory == (
        "last_prompt: Read this project's README.",
        "last_response: frontend readme loaded",
    )
    assert backend_memory == (
        "last_prompt: Read this project's README.",
        "last_response: backend cannot load frontend readme",
    )

    frontend_tool_messages = _tool_messages(frontend_provider)
    backend_tool_messages = _tool_messages(backend_provider)
    assert any("Frontend Shop" in message.content for message in frontend_tool_messages)
    assert any("checkout UI" in message.content for message in frontend_tool_messages)
    assert any(message.metadata["is_error"] is True for message in backend_tool_messages)
    assert any(
        "read path does not exist: README.md" in message.content
        for message in backend_tool_messages
    )


def _build_project_app(
    *,
    state_dir: Path,
    workdir: Path,
    provider: ReadReadmeProvider,
):
    return build_application(
        Settings.from_env(
            {
                "TINY_CLAW_STATE_DIR": str(state_dir),
                "TINY_CLAW_WORKDIR": str(workdir),
                "TINY_CLAW_ENABLED_TOOLS": "read",
            }
        ),
        provider=provider,
    )


def _tool_messages(provider: ReadReadmeProvider) -> list[Message]:
    return [
        message
        for request in provider.requests
        for message in request.messages
        if message.role is Role.TOOL
    ]
