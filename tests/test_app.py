from __future__ import annotations

import pytest

from tiny_claw._internal.app import build_application, build_integration_application
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse
from tiny_claw._internal.schema.message import Message
from tiny_claw._internal.session import SessionRef
from tiny_claw._internal.settings import Settings


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "fake-provider"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            message=Message.assistant(content="fake"),
            provider=self.name,
            model="fake-model",
        )


def test_application_supports_claude_alias(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "anthropic",
            "CLAUDE_KEY": "key",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    app = build_application(settings)

    assert app.engine.provider_name == "claude"


def test_application_rejects_missing_openai_key(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "openai",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    with pytest.raises(ConfigurationError, match="OPENAI"):
        build_application(settings)


def test_application_passes_openai_base_url(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "openai",
            "OPENAI_KEY": "key",
            "OPENAI_BASE_URL": "https://openai.example/v1",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    app = build_application(settings)

    assert app.settings.openai_base_url == "https://openai.example/v1"


def test_application_uses_injected_provider(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path)})

    app = build_application(settings, provider=FakeProvider())

    assert app.engine.provider_name == "fake-provider"


def test_application_uses_default_cli_session_when_session_is_omitted(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path)})
    provider = FakeProvider()
    app = build_application(settings, provider=provider)

    result = app.run(prompt="hello", max_steps=1)

    session = app.session_manager.resolve_cli(None)
    memory = app.session_manager.memory_store(session)
    assert result.workdir == settings.workdir
    assert memory.read_recent(limit=2) == (
        "last_prompt: hello",
        "last_response: fake",
    )
    assert not hasattr(provider.requests[0], "session")


def test_application_isolates_named_cli_sessions(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path)})
    app = build_application(settings, provider=FakeProvider())
    first = app.session_manager.resolve_cli("first")
    second = app.session_manager.resolve_cli("second")

    app.run(prompt="hello first", max_steps=1, session=first)
    app.run(prompt="hello second", max_steps=1, session=second)

    assert app.session_manager.memory_store(first).read_recent(limit=2) == (
        "last_prompt: hello first",
        "last_response: fake",
    )
    assert app.session_manager.memory_store(second).read_recent(limit=2) == (
        "last_prompt: hello second",
        "last_response: fake",
    )


def test_application_rejects_session_from_different_workdir(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path / "state")})
    app = build_application(settings, provider=FakeProvider())
    other_workdir = tmp_path / "other"
    other_workdir.mkdir()
    session = SessionRef(
        key="foreign",
        source="test",
        external_id="foreign",
        workdir=other_workdir,
        display_name="foreign",
    )

    with pytest.raises(ValueError, match="session workdir"):
        app.run(prompt="hello", max_steps=1, session=session)


def test_integration_application_uses_openai_even_when_default_provider_is_echo(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "OPENAI_KEY": "key",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    app = build_integration_application(settings)

    assert app.engine.provider_name == "openai"


def test_application_registers_read_tool(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path)})

    app = build_application(settings)

    assert app.health().tools == ("read",)


def test_application_registers_explicitly_enabled_tools(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_STATE_DIR": str(tmp_path),
            "TINY_CLAW_ENABLED_TOOLS": "read,write,bash,edit",
        }
    )

    app = build_application(settings)

    assert app.health().tools == ("bash", "edit", "read", "write")
