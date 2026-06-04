"""Runtime settings for tiny-claw."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from tiny_claw._internal.errors import ConfigurationError

DEFAULT_STATE_DIR = Path.home() / ".tiny-claw"
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DEFAULT_MAX_TOKENS = 1024
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
DEFAULT_ENABLED_TOOLS = ("read",)
SUPPORTED_TOOLS = {"bash", "edit", "read", "write"}
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 8000
DEFAULT_FEISHU_EVENT_PATH = "/api/events/feishu"


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    provider_name: str = "echo"
    model: str = "echo"
    max_tokens: int = DEFAULT_MAX_TOKENS
    state_dir: Path = DEFAULT_STATE_DIR
    workdir: Path = field(default_factory=lambda: Path.cwd().resolve())
    enabled_tools: tuple[str, ...] = DEFAULT_ENABLED_TOOLS
    server_host: str = DEFAULT_SERVER_HOST
    server_port: int = DEFAULT_SERVER_PORT
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    claude_api_key: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_verification_token: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_event_path: str = DEFAULT_FEISHU_EVENT_PATH

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        log_level: str | None = None,
    ) -> Self:
        env = _load_environment(environ)
        resolved_log_level = log_level or env.get("TINY_CLAW_LOG_LEVEL", "INFO")
        provider_name = env.get("TINY_CLAW_PROVIDER", "echo").lower()
        model = env.get("TINY_CLAW_MODEL") or _default_model(provider_name)
        max_tokens = _positive_int_env(env.get("TINY_CLAW_MAX_TOKENS"), DEFAULT_MAX_TOKENS)
        state_dir = Path(env.get("TINY_CLAW_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
        workdir = Path(env.get("TINY_CLAW_WORKDIR", str(Path.cwd()))).expanduser().resolve()
        enabled_tools = _enabled_tools_env(env.get("TINY_CLAW_ENABLED_TOOLS"))
        server_port = _positive_int_env(env.get("TINY_CLAW_SERVER_PORT"), DEFAULT_SERVER_PORT)

        return cls(
            log_level=_normalize_log_level(resolved_log_level),
            provider_name=provider_name,
            model=model,
            max_tokens=max_tokens,
            state_dir=state_dir,
            workdir=workdir,
            enabled_tools=enabled_tools,
            server_host=env.get("TINY_CLAW_SERVER_HOST", DEFAULT_SERVER_HOST),
            server_port=server_port,
            openai_api_key=_first_env(
                env,
                "OPENAI_API_KEY",
                "OPENAI_KEY",
                "TINY_CLAW_OPENAI_API_KEY",
            ),
            openai_base_url=_first_env(
                env,
                "OPENAI_BASE_URL",
                "TINY_CLAW_OPENAI_BASE_URL",
            ),
            claude_api_key=_first_env(
                env,
                "ANTHROPIC_API_KEY",
                "CLAUDE_KEY",
                "TINY_CLAW_CLAUDE_API_KEY",
            ),
            feishu_app_id=_first_env(env, "FEISHU_APP_ID", "LARK_APP_ID"),
            feishu_app_secret=_first_env(env, "FEISHU_APP_SECRET", "LARK_APP_SECRET"),
            feishu_verification_token=env.get("FEISHU_VERIFICATION_TOKEN"),
            feishu_encrypt_key=env.get("FEISHU_ENCRYPT_KEY"),
            feishu_event_path=_event_path_env(env.get("FEISHU_EVENT_PATH")),
        )


def _normalize_log_level(value: str) -> str:
    normalized = value.upper()
    if normalized not in LOG_LEVELS:
        raise ConfigurationError(
            f"Invalid log level {value!r}; expected one of {', '.join(sorted(LOG_LEVELS))}"
        )
    return normalized


def _default_model(provider_name: str) -> str:
    if provider_name == "openai":
        return DEFAULT_OPENAI_MODEL
    if provider_name in {"claude", "anthropic"}:
        return DEFAULT_CLAUDE_MODEL
    return provider_name


def _first_env(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _positive_int_env(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(
            f"Invalid TINY_CLAW_MAX_TOKENS {value!r}; expected integer"
        ) from exc
    if parsed < 1:
        raise ConfigurationError("TINY_CLAW_MAX_TOKENS must be greater than or equal to 1")
    return parsed


def _event_path_env(value: str | None) -> str:
    if value is None:
        return DEFAULT_FEISHU_EVENT_PATH
    path = value.strip()
    if not path:
        raise ConfigurationError("FEISHU_EVENT_PATH must not be empty")
    if not path.startswith("/"):
        raise ConfigurationError("FEISHU_EVENT_PATH must start with '/'")
    return path


def _enabled_tools_env(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_ENABLED_TOOLS

    names = tuple(name.strip().lower() for name in value.split(",") if name.strip())
    unknown = sorted(set(names) - SUPPORTED_TOOLS)
    if unknown:
        raise ConfigurationError(
            f"Invalid TINY_CLAW_ENABLED_TOOLS {', '.join(unknown)!r}; "
            f"expected tools from: {', '.join(sorted(SUPPORTED_TOOLS))}"
        )
    return tuple(sorted(set(names)))


def _load_environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    if environ is not None:
        return environ

    merged = dict(_read_dotenv(Path.cwd() / ".env"))
    merged.update(os.environ)
    return merged


def _read_dotenv(path: Path) -> Mapping[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ConfigurationError(f"Invalid .env line {line_number}: expected KEY=VALUE")

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigurationError(f"Invalid .env line {line_number}: empty key")
        values[key] = _parse_dotenv_value(value.strip())
    return values


def _parse_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
