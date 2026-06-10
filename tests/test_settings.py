from __future__ import annotations

import pytest

from tiny_claw._internal import settings as settings_module
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.settings import Settings


def test_settings_uses_current_directory_as_default_workdir() -> None:
    settings = Settings.from_env({})

    assert settings.provider_name == "openai"
    assert settings.model == "gpt-5.4"
    assert settings.workdir.is_absolute()
    assert settings.workdir.exists()


def test_settings_reads_workdir_from_environment(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_WORKDIR": str(tmp_path)})

    assert settings.workdir == tmp_path.resolve()


def test_settings_defaults_to_read_tool_only() -> None:
    settings = Settings.from_env({})

    assert settings.enabled_tools == ("read",)


def test_settings_uses_internal_context_compactor_defaults() -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_CONTEXT_MAX_CHARS": "10",
            "TINY_CLAW_CONTEXT_RETAIN_LAST_MESSAGES": "2",
        }
    )

    assert settings.context_max_chars == 120_000
    assert settings.context_retain_last_messages == 8
    assert settings.context_old_tool_result_mask_chars == 240
    assert settings.context_recent_tool_result_head_chars == 2_000
    assert settings.context_recent_tool_result_tail_chars == 2_000


def test_settings_reads_enabled_tools_from_environment() -> None:
    settings = Settings.from_env({"TINY_CLAW_ENABLED_TOOLS": "read,write,bash,edit,explore,read"})

    assert settings.enabled_tools == ("bash", "edit", "explore", "read", "write")


def test_settings_reads_runtime_tool_policy_and_approval_configuration() -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_TOOL_ALLOWLIST": "read,write",
            "TINY_CLAW_TOOL_DENYLIST": "bash",
            "TINY_CLAW_APPROVAL_REQUIRED_TOOLS": "write,edit",
            "TINY_CLAW_APPROVAL_PROVIDER": "feishu",
            "TINY_CLAW_APPROVAL_TIMEOUT_SECONDS": "120",
        }
    )

    assert settings.tool_allowlist == ("read", "write")
    assert settings.tool_denylist == ("bash",)
    assert settings.approval_required_tools == ("edit", "write")
    assert settings.approval_provider == "feishu"
    assert settings.approval_timeout_seconds == 120


def test_settings_rejects_invalid_approval_provider() -> None:
    with pytest.raises(ConfigurationError, match="APPROVAL_PROVIDER"):
        Settings.from_env({"TINY_CLAW_APPROVAL_PROVIDER": "slack"})


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


def test_settings_reads_server_and_feishu_configuration() -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_SERVER_HOST": "127.0.0.1",
            "TINY_CLAW_SERVER_PORT": "9000",
            "FEISHU_APP_ID": "cli_xxx",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_VERIFICATION_TOKEN": "token",
            "FEISHU_ENCRYPT_KEY": "encrypt",
            "FEISHU_EVENT_PATH": "/api/events/custom-feishu",
        }
    )

    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 9000
    assert settings.feishu_app_id == "cli_xxx"
    assert settings.feishu_app_secret == "secret"
    assert settings.feishu_verification_token == "token"
    assert settings.feishu_encrypt_key == "encrypt"
    assert settings.feishu_event_path == "/api/events/custom-feishu"


def test_settings_accepts_lark_feishu_aliases() -> None:
    settings = Settings.from_env(
        {
            "LARK_APP_ID": "cli_lark",
            "LARK_APP_SECRET": "lark-secret",
        }
    )

    assert settings.feishu_app_id == "cli_lark"
    assert settings.feishu_app_secret == "lark-secret"


def test_settings_rejects_invalid_feishu_event_path() -> None:
    with pytest.raises(ConfigurationError, match="FEISHU_EVENT_PATH"):
        Settings.from_env({"FEISHU_EVENT_PATH": "api/events/feishu"})


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
    monkeypatch.setattr(settings_module, "_source_tree_dotenv_path", lambda: None)
    monkeypatch.delenv("TINY_CLAW_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    settings = Settings.from_env()

    assert settings.provider_name == "openai"
    assert settings.openai_api_key == "dotenv-key"
    assert settings.openai_base_url == "https://dotenv.example/v1"


def test_project_dotenv_takes_priority_and_environment_fills_missing_values(
    monkeypatch,
    tmp_path,
) -> None:
    project_dotenv = tmp_path / "project" / ".env"
    project_dotenv.parent.mkdir()
    project_dotenv.write_text("OPENAI_API_KEY=project-key\n", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=cwd-key",
                "OPENAI_BASE_URL=https://cwd.example/v1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings_module,
        "_source_tree_dotenv_path",
        lambda: project_dotenv,
    )
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("TINY_CLAW_MAX_TOKENS", "2048")

    settings = Settings.from_env()

    assert settings.openai_api_key == "project-key"
    assert settings.openai_base_url == "https://cwd.example/v1"
    assert settings.max_tokens == 2048


def test_current_directory_dotenv_is_used_when_project_dotenv_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=cwd-key\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_source_tree_dotenv_path", lambda: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env()

    assert settings.openai_api_key == "cwd-key"
