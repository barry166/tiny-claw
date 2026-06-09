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
DEFAULT_CONTEXT_MAX_CHARS = 120_000
DEFAULT_CONTEXT_RETAIN_LAST_MESSAGES = 8
DEFAULT_CONTEXT_OLD_TOOL_RESULT_MASK_CHARS = 240
DEFAULT_CONTEXT_RECENT_TOOL_RESULT_HEAD_CHARS = 2_000
DEFAULT_CONTEXT_RECENT_TOOL_RESULT_TAIL_CHARS = 2_000
DEFAULT_APPROVAL_REQUIRED_TOOLS = ("bash", "edit", "write")
APPROVAL_PROVIDERS = {"off", "feishu"}


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    provider_name: str = "openai"
    model: str = DEFAULT_OPENAI_MODEL
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
    context_max_chars: int = DEFAULT_CONTEXT_MAX_CHARS
    context_retain_last_messages: int = DEFAULT_CONTEXT_RETAIN_LAST_MESSAGES
    context_old_tool_result_mask_chars: int = DEFAULT_CONTEXT_OLD_TOOL_RESULT_MASK_CHARS
    context_recent_tool_result_head_chars: int = DEFAULT_CONTEXT_RECENT_TOOL_RESULT_HEAD_CHARS
    context_recent_tool_result_tail_chars: int = DEFAULT_CONTEXT_RECENT_TOOL_RESULT_TAIL_CHARS
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    approval_provider: str = "off"
    approval_timeout_seconds: int = 3600

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        log_level: str | None = None,
    ) -> Self:
        env = _load_environment(environ)
        resolved_log_level = log_level or env.get("TINY_CLAW_LOG_LEVEL", "INFO")
        provider_name = env.get("TINY_CLAW_PROVIDER", "openai").lower()
        model = env.get("TINY_CLAW_MODEL") or _default_model(provider_name)
        max_tokens = _positive_int_env(env.get("TINY_CLAW_MAX_TOKENS"), DEFAULT_MAX_TOKENS)
        state_dir = Path(env.get("TINY_CLAW_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
        workdir = Path(env.get("TINY_CLAW_WORKDIR", str(Path.cwd()))).expanduser().resolve()
        enabled_tools = _enabled_tools_env(env.get("TINY_CLAW_ENABLED_TOOLS"))
        server_port = _positive_int_env(env.get("TINY_CLAW_SERVER_PORT"), DEFAULT_SERVER_PORT)
        approval_provider = env.get("TINY_CLAW_APPROVAL_PROVIDER", "off").strip().lower()
        if approval_provider not in APPROVAL_PROVIDERS:
            raise ConfigurationError(
                "Invalid TINY_CLAW_APPROVAL_PROVIDER "
                f"{approval_provider!r}; expected one of: {', '.join(sorted(APPROVAL_PROVIDERS))}"
            )

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
            tool_allowlist=_optional_tools_env(env.get("TINY_CLAW_TOOL_ALLOWLIST")),
            tool_denylist=_optional_tools_env(env.get("TINY_CLAW_TOOL_DENYLIST")),
            approval_required_tools=_optional_tools_env(
                env.get("TINY_CLAW_APPROVAL_REQUIRED_TOOLS"),
                default=DEFAULT_APPROVAL_REQUIRED_TOOLS,
            ),
            approval_provider=approval_provider,
            approval_timeout_seconds=_positive_int_env(
                env.get("TINY_CLAW_APPROVAL_TIMEOUT_SECONDS"),
                3600,
            ),
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


def _optional_tools_env(
    value: str | None,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if value is None:
        return default
    names = tuple(name.strip().lower() for name in value.split(",") if name.strip())
    unknown = sorted(set(names) - SUPPORTED_TOOLS)
    if unknown:
        raise ConfigurationError(
            f"Invalid tool policy value {', '.join(unknown)!r}; "
            f"expected tools from: {', '.join(sorted(SUPPORTED_TOOLS))}"
        )
    return tuple(sorted(set(names)))


def _load_environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    if environ is not None:
        return environ

    merged: dict[str, str] = {}
    for dotenv_path in _dotenv_paths():
        for key, value in _read_dotenv(dotenv_path).items():
            merged.setdefault(key, value)
    for key, value in os.environ.items():
        merged.setdefault(key, value)
    return merged


def _dotenv_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    source_tree_dotenv = _source_tree_dotenv_path()
    if source_tree_dotenv is not None:
        paths.append(source_tree_dotenv)

    cwd_dotenv = Path.cwd() / ".env"
    if cwd_dotenv not in paths:
        paths.append(cwd_dotenv)
    return tuple(paths)


def _source_tree_dotenv_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            content = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        if 'name = "tiny-claw"' in content:
            return parent / ".env"
    return None


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
