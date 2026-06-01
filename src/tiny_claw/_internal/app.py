"""Application assembly for tiny-claw."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.engine.main_loop import MainLoop, RunMode, RunResult
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider
from tiny_claw._internal.provider.claude import ClaudeProvider
from tiny_claw._internal.provider.echo import EchoProvider
from tiny_claw._internal.provider.openai import OpenAIProvider
from tiny_claw._internal.settings import Settings
from tiny_claw._internal.tools.base import Tool
from tiny_claw._internal.tools.builtin.bash import BashTool
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
    ) -> RunResult:
        return self.engine.run(prompt=prompt, max_steps=max_steps, mode=mode)


def build_application(settings: Settings) -> Application:
    provider = _build_provider(settings)
    memory = FileMemoryStore(settings.state_dir)
    tools = _build_tool_registry(settings.workdir, enabled_tools=settings.enabled_tools)
    engine = MainLoop(
        provider=provider,
        context_builder=ContextBuilder(),
        memory=memory,
        tools=tools,
        workdir=settings.workdir,
    )
    return Application(settings=settings, engine=engine, tools=tools)


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


def _build_tool_registry(
    workdir: Path | None = None,
    *,
    enabled_tools: tuple[str, ...] = ("read",),
) -> ToolRegistry:
    resolved_workdir = Path.cwd().resolve() if workdir is None else workdir.resolve()
    registry = ToolRegistry()
    available_tools: dict[str, Tool] = {
        "bash": BashTool(workdir=resolved_workdir),
        "read": ReadTool(root=resolved_workdir),
        "write": WriteTool(root=resolved_workdir),
    }
    for name in enabled_tools:
        registry.register(available_tools[name])
    return registry
