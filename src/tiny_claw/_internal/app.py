"""Application assembly for tiny-claw."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tiny_claw._internal.context import ContextBuilder, ContextCompactor
from tiny_claw._internal.engine.channel import Channel
from tiny_claw._internal.engine.main_loop import MainLoop, RunMode, RunResult
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.provider.base import LLMProvider
from tiny_claw._internal.provider.claude import ClaudeProvider
from tiny_claw._internal.provider.echo import EchoProvider
from tiny_claw._internal.provider.openai import OpenAIProvider
from tiny_claw._internal.session import SessionManager, SessionMemoryStore, SessionRef
from tiny_claw._internal.settings import DEFAULT_OPENAI_MODEL, Settings
from tiny_claw._internal.tools.base import Tool
from tiny_claw._internal.tools.builtin.bash import BashTool
from tiny_claw._internal.tools.builtin.edit import EditTool
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.builtin.write import WriteTool
from tiny_claw._internal.tools.registry import ToolRegistry


@dataclass(frozen=True)
class HealthReport:
    status: str
    provider: str
    tools: tuple[str, ...]
    state_dir: Path
    workdir: Path

    def render(self) -> str:
        tools = ", ".join(self.tools) if self.tools else "none"
        return (
            f"status={self.status}\n"
            f"provider={self.provider}\n"
            f"tools={tools}\n"
            f"state_dir={self.state_dir}\n"
            f"workdir={self.workdir}"
        )


@dataclass(frozen=True)
class Application:
    settings: Settings
    engine: MainLoop
    tools: ToolRegistry
    session_manager: SessionManager

    def health(self) -> HealthReport:
        return HealthReport(
            status="ok",
            provider=self.engine.provider_name,
            tools=self.tools.names(),
            state_dir=self.settings.state_dir,
            workdir=self.settings.workdir,
        )

    def run(
        self,
        *,
        prompt: str,
        max_steps: int,
        mode: RunMode = RunMode.ACT,
        session: SessionRef | None = None,
        channel: Channel | None = None,
    ) -> RunResult:
        resolved_session = session or self.session_manager.resolve_cli(None)
        if resolved_session.workdir.resolve() != self.settings.workdir.resolve():
            raise ValueError(
                "session workdir must match application workdir because tools are "
                "registered for the application workdir"
            )
        return self.engine.run(
            prompt=prompt,
            max_steps=max_steps,
            mode=mode,
            session=resolved_session,
            channel=channel,
        )


def build_application(
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
) -> Application:
    resolved_provider = provider if provider is not None else _build_provider(settings)
    session_manager = SessionManager(
        state_dir=settings.state_dir,
        workdir=settings.workdir,
    )
    memory = SessionMemoryStore(settings.state_dir)
    tools = _build_tool_registry(settings.workdir, enabled_tools=settings.enabled_tools)
    engine = MainLoop(
        provider=resolved_provider,
        context_builder=ContextBuilder(workdir=settings.workdir),
        context_compactor=ContextCompactor(
            max_chars=settings.context_max_chars,
            retain_last_messages=settings.context_retain_last_messages,
            old_tool_result_mask_chars=settings.context_old_tool_result_mask_chars,
            recent_tool_result_head_chars=settings.context_recent_tool_result_head_chars,
            recent_tool_result_tail_chars=settings.context_recent_tool_result_tail_chars,
        ),
        memory=memory,
        tools=tools,
    )
    return Application(
        settings=settings,
        engine=engine,
        tools=tools,
        session_manager=session_manager,
    )


def build_integration_application(settings: Settings) -> Application:
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        model=_integration_model(settings),
        max_tokens=settings.max_tokens,
        base_url=settings.openai_base_url,
    )
    return build_application(settings, provider=provider)


def _build_provider(settings: Settings) -> LLMProvider:
    provider_name = settings.provider_name.lower()
    if provider_name == "echo":
        return EchoProvider(model=settings.model)
    if provider_name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
            base_url=settings.openai_base_url,
        )
    if provider_name in {"claude", "anthropic"}:
        return ClaudeProvider(
            api_key=settings.claude_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
        )
    raise ConfigurationError(f"Unsupported provider: {settings.provider_name}")


def _integration_model(settings: Settings) -> str:
    if settings.provider_name == "openai":
        return settings.model
    return DEFAULT_OPENAI_MODEL


def _build_tool_registry(
    workdir: Path | None = None,
    *,
    enabled_tools: tuple[str, ...] = ("read",),
) -> ToolRegistry:
    resolved_workdir = Path.cwd().resolve() if workdir is None else workdir.resolve()
    registry = ToolRegistry()
    available_tools: dict[str, Tool] = {
        "bash": BashTool(workdir=resolved_workdir),
        "edit": EditTool(root=resolved_workdir),
        "read": ReadTool(root=resolved_workdir),
        "write": WriteTool(root=resolved_workdir),
    }
    for name in enabled_tools:
        registry.register(available_tools[name])
    return registry
