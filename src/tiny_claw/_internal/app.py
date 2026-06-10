"""Application assembly for tiny-claw."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tiny_claw._internal.approval import (
    ApprovalDecision,
    ApprovalResumeResult,
    DefaultRiskPolicy,
    FileApprovalStore,
    FileRunCheckpointStore,
    HumanApprovalMiddleware,
)
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
from tiny_claw._internal.subagent import SubagentRunner
from tiny_claw._internal.tools.base import Tool
from tiny_claw._internal.tools.builtin.bash import BashTool
from tiny_claw._internal.tools.builtin.edit import EditTool
from tiny_claw._internal.tools.builtin.explore import ExplorerSubagentTool
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.builtin.write import WriteTool
from tiny_claw._internal.tools.policy import ToolPolicyMiddleware
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
    approval_store: FileApprovalStore
    checkpoint_store: FileRunCheckpointStore

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

    def resume_approval(
        self,
        *,
        approval_id: str,
        decision: ApprovalDecision,
        session: SessionRef,
        reason: str | None = None,
        channel: Channel | None = None,
    ) -> ApprovalResumeResult:
        record = self.approval_store.find(approval_id)
        if record is None:
            return ApprovalResumeResult(ok=False, message=f"未找到审批请求：{approval_id}")
        if record.session_key != session.key:
            return ApprovalResumeResult(ok=False, message="审批请求不属于当前会话，已拒绝。")
        if record.status != "pending":
            return ApprovalResumeResult(
                ok=False,
                message=f"审批请求 {approval_id} 当前状态为 {record.status}，不能重复处理。",
            )
        if record.is_expired:
            self.approval_store.expire(record)
            return ApprovalResumeResult(ok=False, message=f"审批请求 {approval_id} 已过期。")
        if decision == "reject":
            rejected = self.approval_store.reject(record, reason=reason)
            result = self.engine.resume_rejected_approval(
                approval=rejected,
                session=session,
                channel=channel,
            )
            return ApprovalResumeResult(
                ok=True,
                message=f"已拒绝审批 {approval_id}。",
                result_text=result.text,
            )
        approved = self.approval_store.approve(record, reason=reason)
        result = self.engine.resume_approved_approval(
            approval=approved,
            session=session,
            channel=channel,
        )
        return ApprovalResumeResult(
            ok=True,
            message=f"已批准审批 {approval_id}。",
            result_text=result.text,
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
    context_builder = ContextBuilder(workdir=settings.workdir)
    context_compactor = ContextCompactor(
        max_chars=settings.context_max_chars,
        retain_last_messages=settings.context_retain_last_messages,
        old_tool_result_mask_chars=settings.context_old_tool_result_mask_chars,
        recent_tool_result_head_chars=settings.context_recent_tool_result_head_chars,
        recent_tool_result_tail_chars=settings.context_recent_tool_result_tail_chars,
    )
    subagent_runner = SubagentRunner(
        provider=resolved_provider,
        context_builder=context_builder,
        context_compactor=context_compactor,
        memory=memory,
    )
    tools = _build_tool_registry(
        settings.workdir,
        enabled_tools=settings.enabled_tools,
        subagent_runner=subagent_runner,
    )
    approval_store = FileApprovalStore(settings.state_dir)
    checkpoint_store = FileRunCheckpointStore(settings.state_dir)
    _register_tool_middlewares(
        tools,
        settings=settings,
        approval_store=approval_store,
        checkpoint_store=checkpoint_store,
    )
    engine = MainLoop(
        provider=resolved_provider,
        context_builder=context_builder,
        context_compactor=context_compactor,
        memory=memory,
        tools=tools,
        checkpoint_store=checkpoint_store,
    )
    return Application(
        settings=settings,
        engine=engine,
        tools=tools,
        session_manager=session_manager,
        approval_store=approval_store,
        checkpoint_store=checkpoint_store,
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
    subagent_runner: SubagentRunner | None = None,
) -> ToolRegistry:
    resolved_workdir = Path.cwd().resolve() if workdir is None else workdir.resolve()
    registry = ToolRegistry()
    available_tools: dict[str, Tool] = {
        "bash": BashTool(workdir=resolved_workdir),
        "edit": EditTool(root=resolved_workdir),
        "read": ReadTool(root=resolved_workdir),
        "write": WriteTool(root=resolved_workdir),
    }
    if subagent_runner is not None:
        available_tools["explore"] = ExplorerSubagentTool(runner=subagent_runner)
    for name in enabled_tools:
        registry.register(available_tools[name])
    return registry


def _register_tool_middlewares(
    registry: ToolRegistry,
    *,
    settings: Settings,
    approval_store: FileApprovalStore,
    checkpoint_store: FileRunCheckpointStore,
) -> None:
    registry.use(
        ToolPolicyMiddleware(
            allowlist=settings.tool_allowlist,
            denylist=settings.tool_denylist,
        )
    )
    if settings.approval_provider == "off":
        return
    registry.use(
        HumanApprovalMiddleware(
            approval_store=approval_store,
            checkpoint_store=checkpoint_store,
            risk_policy=DefaultRiskPolicy(approval_required_tools=settings.approval_required_tools),
            timeout_seconds=settings.approval_timeout_seconds,
        )
    )
