from __future__ import annotations

import pytest

from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.settings import Settings


def test_settings_uses_current_directory_as_default_workdir() -> None:
    settings = Settings.from_env({})

    assert settings.workdir.is_absolute()
    assert settings.workdir.exists()


def test_settings_reads_workdir_from_environment(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_WORKDIR": str(tmp_path)})

    assert settings.workdir == tmp_path.resolve()


def test_settings_defaults_to_read_tool_only() -> None:
    settings = Settings.from_env({})

    assert settings.enabled_tools == ("read",)


def test_settings_reads_enabled_tools_from_environment() -> None:
    settings = Settings.from_env({"TINY_CLAW_ENABLED_TOOLS": "read,write,bash,edit,read"})

    assert settings.enabled_tools == ("bash", "edit", "read", "write")


def test_settings_rejects_unknown_enabled_tool() -> None:
    with pytest.raises(ConfigurationError, match="ENABLED_TOOLS"):
        Settings.from_env({"TINY_CLAW_ENABLED_TOOLS": "read,unknown"})


def test_settings_reads_provider_specific_defaults_and_keys() -> None:
    openai_settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "openai",
            "OPENAI_KEY": "openai-key",
            "OPENAI_BASE_URL": "https://openai.example/v1",
            "TINY_CLAW_MAX_TOKENS": "2048",
        }
    )
    claude_settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "claude",
            "CLAUDE_KEY": "claude-key",
        }
    )

    assert openai_settings.model == "gpt-5.4"
    assert openai_settings.openai_api_key == "openai-key"
    assert openai_settings.openai_base_url == "https://openai.example/v1"
    assert openai_settings.max_tokens == 2048
    assert claude_settings.model == "claude-sonnet-4-20250514"
    assert claude_settings.claude_api_key == "claude-key"


def test_settings_prefers_explicit_model() -> None:
    settings = Settings.from_env({"TINY_CLAW_PROVIDER": "openai", "TINY_CLAW_MODEL": "custom"})

    assert settings.model == "custom"


def test_settings_rejects_invalid_max_tokens() -> None:
    with pytest.raises(ConfigurationError, match="MAX_TOKENS"):
        Settings.from_env({"TINY_CLAW_MAX_TOKENS": "0"})


def test_settings_reads_dotenv_when_no_explicit_environment(monkeypatch, tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "TINY_CLAW_PROVIDER=openai",
                "OPENAI_API_KEY=dotenv-key",
                "OPENAI_BASE_URL=https://dotenv.example/v1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TINY_CLAW_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    settings = Settings.from_env()

    assert settings.provider_name == "openai"
    assert settings.openai_api_key == "dotenv-key"
    assert settings.openai_base_url == "https://dotenv.example/v1"


def test_environment_overrides_dotenv(monkeypatch, tmp_path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env()

    assert settings.openai_api_key == "env-key"
