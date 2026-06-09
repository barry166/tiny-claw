"""Human approval state and middleware for high-risk tool calls."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from tiny_claw._internal.schema.message import Message, ToolCall
from tiny_claw._internal.session import SessionRef
from tiny_claw._internal.tools.middleware import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolNext,
    ToolSuspension,
)

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "consumed"]
ApprovalDecision = Literal["approve", "reject"]
RiskAction = Literal["allow", "deny", "approval_required"]
APPROVAL_METADATA_KEY = "approval_id"
CHECKPOINT_METADATA_KEY = "checkpoint_id"
CHECKPOINT_DRAFT_METADATA_KEY = "checkpoint_draft"
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 3600


@dataclass(frozen=True)
class RiskDecision:
    action: RiskAction
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    session_key: str
    session_source: str
    session_external_id: str
    tool_call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    tool_call_hash: str
    risk_reasons: tuple[str, ...]
    checkpoint_id: str
    status: ApprovalStatus
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decision_reason: str | None = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= _parse_datetime(self.expires_at)


@dataclass(frozen=True)
class RunCheckpoint:
    id: str
    session_key: str
    mode: str
    prompt: str
    step: int
    max_steps: int
    phase: str
    tool_policy: str
    provider: str
    current_plan_todo_id: str | None
    current_step_had_tool_error: bool
    plan_required: bool
    visible_tool_names: tuple[str, ...]
    messages: tuple[Message, ...]
    pending_tool_calls: tuple[ToolCall, ...]
    pending_index: int
    created_at: str


@dataclass(frozen=True)
class RunCheckpointDraft:
    mode: str
    prompt: str
    step: int
    max_steps: int
    phase: str
    tool_policy: str
    provider: str
    current_plan_todo_id: str | None
    current_step_had_tool_error: bool
    plan_required: bool
    visible_tool_names: tuple[str, ...]
    messages: tuple[Message, ...]
    pending_tool_calls: tuple[ToolCall, ...]
    pending_index: int


@dataclass(frozen=True)
class ApprovalRequest:
    approval: ApprovalRecord
    session: SessionRef
    workdir: Path


@dataclass(frozen=True)
class ApprovalDispatchResult:
    delivered: bool
    detail: str = ""


class ApprovalRequester(Protocol):
    def request_approval(self, request: ApprovalRequest) -> ApprovalDispatchResult:
        """Send an approval request through an external channel."""


class NullApprovalRequester:
    def request_approval(self, request: ApprovalRequest) -> ApprovalDispatchResult:
        return ApprovalDispatchResult(delivered=True, detail="approval request persisted")


@dataclass(frozen=True)
class ApprovalResumeResult:
    ok: bool
    message: str
    result_text: str | None = None


@dataclass(frozen=True)
class FileApprovalStore:
    state_dir: Path

    def create(
        self,
        *,
        session: SessionRef,
        tool_call: ToolCall,
        checkpoint_id: str,
        risk_reasons: Sequence[str],
        timeout_seconds: int,
    ) -> ApprovalRecord:
        now = datetime.now(UTC)
        approval_id = uuid.uuid4().hex[:12]
        record = ApprovalRecord(
            id=approval_id,
            session_key=session.key,
            session_source=session.source,
            session_external_id=session.external_id,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=dict(tool_call.arguments),
            tool_call_hash=tool_call_hash(tool_call),
            risk_reasons=tuple(risk_reasons),
            checkpoint_id=checkpoint_id,
            status="pending",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=timeout_seconds)).isoformat(),
        )
        self._write(record)
        return record

    def read(self, session_key: str, approval_id: str) -> ApprovalRecord:
        return approval_record_from_json(
            json.loads(
                self._path(session_key=session_key, approval_id=approval_id).read_text(
                    encoding="utf-8"
                )
            )
        )

    def find(self, approval_id: str) -> ApprovalRecord | None:
        sessions_dir = self.state_dir / "sessions"
        if not sessions_dir.exists():
            return None
        for path in sessions_dir.glob(f"*/approvals/{approval_id}.json"):
            try:
                return approval_record_from_json(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return None

    def approve(
        self,
        record: ApprovalRecord,
        *,
        reason: str | None = None,
    ) -> ApprovalRecord:
        updated = _replace_approval(
            record,
            status="approved",
            decided_at=datetime.now(UTC).isoformat(),
            decision_reason=reason,
        )
        self._write(updated)
        return updated

    def reject(
        self,
        record: ApprovalRecord,
        *,
        reason: str | None = None,
    ) -> ApprovalRecord:
        updated = _replace_approval(
            record,
            status="rejected",
            decided_at=datetime.now(UTC).isoformat(),
            decision_reason=reason,
        )
        self._write(updated)
        return updated

    def consume(self, record: ApprovalRecord) -> ApprovalRecord:
        updated = _replace_approval(record, status="consumed")
        self._write(updated)
        return updated

    def expire(self, record: ApprovalRecord) -> ApprovalRecord:
        updated = _replace_approval(record, status="expired")
        self._write(updated)
        return updated

    def _path(self, *, session_key: str, approval_id: str) -> Path:
        return self.state_dir / "sessions" / session_key / "approvals" / f"{approval_id}.json"

    def _write(self, record: ApprovalRecord) -> None:
        path = self._path(session_key=record.session_key, approval_id=record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(approval_record_to_json(record), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class FileRunCheckpointStore:
    state_dir: Path

    def create(
        self,
        *,
        session: SessionRef,
        mode: str,
        prompt: str,
        step: int,
        max_steps: int,
        phase: str,
        tool_policy: str,
        provider: str,
        current_plan_todo_id: str | None,
        current_step_had_tool_error: bool,
        plan_required: bool,
        visible_tool_names: tuple[str, ...],
        messages: Sequence[Message],
        pending_tool_calls: Sequence[ToolCall],
        pending_index: int,
    ) -> RunCheckpoint:
        now = datetime.now(UTC).isoformat()
        checkpoint_id = uuid.uuid4().hex[:12]
        checkpoint = RunCheckpoint(
            id=checkpoint_id,
            session_key=session.key,
            mode=mode,
            prompt=prompt,
            step=step,
            max_steps=max_steps,
            phase=phase,
            tool_policy=tool_policy,
            provider=provider,
            current_plan_todo_id=current_plan_todo_id,
            current_step_had_tool_error=current_step_had_tool_error,
            plan_required=plan_required,
            visible_tool_names=visible_tool_names,
            messages=tuple(messages),
            pending_tool_calls=tuple(pending_tool_calls),
            pending_index=pending_index,
            created_at=now,
        )
        self.write(checkpoint)
        return checkpoint

    def read(self, *, session_key: str, checkpoint_id: str) -> RunCheckpoint:
        path = self._path(session_key=session_key, checkpoint_id=checkpoint_id)
        return checkpoint_from_json(json.loads(path.read_text(encoding="utf-8")))

    def write(self, checkpoint: RunCheckpoint) -> None:
        path = self._path(session_key=checkpoint.session_key, checkpoint_id=checkpoint.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(checkpoint_to_json(checkpoint), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _path(self, *, session_key: str, checkpoint_id: str) -> Path:
        return self.state_dir / "sessions" / session_key / "checkpoints" / f"{checkpoint_id}.json"


@dataclass(frozen=True)
class DefaultRiskPolicy:
    approval_required_tools: tuple[str, ...] = ("bash", "write", "edit")

    def evaluate(self, ctx: ToolExecutionContext) -> RiskDecision:
        if ctx.tool_name not in self.approval_required_tools:
            return RiskDecision(action="allow")
        if ctx.tool_name == "bash":
            return self._evaluate_bash(ctx)
        if ctx.tool_name in {"write", "edit"}:
            return self._evaluate_file_mutation(ctx)
        return RiskDecision(
            action="approval_required",
            reasons=(f"{ctx.tool_name} 被配置为需要人工审批。",),
        )

    def _evaluate_bash(self, ctx: ToolExecutionContext) -> RiskDecision:
        command = str(ctx.arguments.get("command", "")).strip()
        normalized = command.lower()
        reasons: list[str] = []
        patterns = (
            (r"(^|[;&|]\s*)rm\s+", "删除文件或目录"),
            (r"(^|[;&|]\s*)rmdir\s+", "删除目录"),
            (r"(^|[;&|]\s*)sudo(\s|$)", "提权执行"),
            (r"git\s+reset\s+--hard", "强制重置 git 工作树"),
            (r"git\s+clean\b", "清理未跟踪文件"),
            (r"git\s+push\b.*(--force|-f)", "强制推送"),
            (r"(curl|wget)\b.*\|\s*(sh|bash|zsh)\b", "下载脚本后直接执行"),
            (r"(^|[;&|]\s*)chmod\s+", "修改文件权限"),
            (r"(^|[;&|]\s*)chown\s+", "修改文件所有者"),
            (r"(^|[;&|]\s*)kill(all)?\s+", "终止进程"),
            (r"(^|[;&|]\s*)pkill\s+", "按名称终止进程"),
            (r"(^|[;&|]\s*)dd\s+", "底层块写入命令"),
            (r"(^|[;&|]\s*)mkfs\b", "格式化文件系统"),
            (r"\b(deploy|publish|release)\b", "发布或部署相关命令"),
        )
        for pattern, reason in patterns:
            if re.search(pattern, normalized):
                reasons.append(reason)
        if reasons:
            return RiskDecision(action="approval_required", reasons=tuple(reasons))
        return RiskDecision(action="allow")

    def _evaluate_file_mutation(self, ctx: ToolExecutionContext) -> RiskDecision:
        path = str(ctx.arguments.get("path", "")).strip().lower()
        reasons: list[str] = []
        protected_names = (
            ".env",
            ".env.local",
            ".env.production",
            "pyproject.toml",
            "uv.lock",
            "poetry.lock",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        )
        if any(path == name or path.endswith(f"/{name}") for name in protected_names):
            reasons.append("修改受保护配置或 lock 文件")
        if any(
            segment in path for segment in (".github/workflows/", ".gitlab-ci", "secret", "key")
        ):
            reasons.append("修改 CI 或密钥相关文件")
        if ctx.tool_name == "edit":
            old_text = str(ctx.arguments.get("old_text", ""))
            new_text = str(ctx.arguments.get("new_text", ""))
            removed_lines = len(old_text.splitlines()) - len(new_text.splitlines())
            if removed_lines >= 20:
                reasons.append("一次 edit 删除大量内容")
        if reasons:
            return RiskDecision(action="approval_required", reasons=tuple(reasons))
        return RiskDecision(action="allow")


@dataclass(frozen=True)
class HumanApprovalMiddleware:
    approval_store: FileApprovalStore
    checkpoint_store: FileRunCheckpointStore
    risk_policy: DefaultRiskPolicy = field(default_factory=DefaultRiskPolicy)
    timeout_seconds: int = DEFAULT_APPROVAL_TIMEOUT_SECONDS

    def __call__(self, ctx: ToolExecutionContext, next: ToolNext) -> ToolExecutionResult:
        approved_id = _metadata_str(ctx.metadata, APPROVAL_METADATA_KEY)
        if approved_id:
            return self._execute_approved(ctx=ctx, next=next, approval_id=approved_id)

        decision = self.risk_policy.evaluate(ctx)
        if decision.action == "allow":
            return next(ctx)
        if decision.action == "deny":
            return ToolExecutionResult.denied(
                "工具调用被风险策略拒绝。",
                metadata={"is_error": True, "error_type": "risk_policy_denied"},
            )

        draft = ctx.metadata.get(CHECKPOINT_DRAFT_METADATA_KEY)
        if not isinstance(draft, RunCheckpointDraft):
            return ToolExecutionResult.denied(
                "工具调用需要人工审批，但缺少可恢复 checkpoint。",
                metadata={"is_error": True, "error_type": "approval_checkpoint_missing"},
            )
        checkpoint = self.checkpoint_store.create(
            session=ctx.session,
            mode=draft.mode,
            prompt=draft.prompt,
            step=draft.step,
            max_steps=draft.max_steps,
            phase=draft.phase,
            tool_policy=draft.tool_policy,
            provider=draft.provider,
            current_plan_todo_id=draft.current_plan_todo_id,
            current_step_had_tool_error=draft.current_step_had_tool_error,
            plan_required=draft.plan_required,
            visible_tool_names=draft.visible_tool_names,
            messages=draft.messages,
            pending_tool_calls=draft.pending_tool_calls,
            pending_index=_metadata_int(ctx.metadata, "tool_call_index", draft.pending_index),
        )
        call = ToolCall(id=ctx.tool_call_id, name=ctx.tool_name, arguments=dict(ctx.arguments))
        approval = self.approval_store.create(
            session=ctx.session,
            tool_call=call,
            checkpoint_id=checkpoint.id,
            risk_reasons=decision.reasons,
            timeout_seconds=self.timeout_seconds,
        )
        request = ApprovalRequest(approval=approval, session=ctx.session, workdir=ctx.workdir)
        dispatch = _requester_from_metadata(ctx.metadata).request_approval(request)
        content = render_approval_required(approval, delivered=dispatch.delivered)
        return ToolExecutionResult.suspended(
            ToolSuspension(
                approval_id=approval.id,
                checkpoint_id=approval.checkpoint_id,
                reason="; ".join(approval.risk_reasons),
                content=content,
                metadata={
                    "approval_id": approval.id,
                    "checkpoint_id": approval.checkpoint_id,
                    "approval_delivered": dispatch.delivered,
                },
            ),
            metadata={
                "is_error": True,
                "error_type": "tool_approval_required",
                "approval_id": approval.id,
                "checkpoint_id": approval.checkpoint_id,
            },
        )

    def _execute_approved(
        self,
        *,
        ctx: ToolExecutionContext,
        next: ToolNext,
        approval_id: str,
    ) -> ToolExecutionResult:
        record = self.approval_store.read(ctx.session.key, approval_id)
        call = ToolCall(id=ctx.tool_call_id, name=ctx.tool_name, arguments=dict(ctx.arguments))
        if record.status != "approved":
            return ToolExecutionResult.denied(
                f"审批 {approval_id} 当前状态为 {record.status}，不能执行工具。",
                metadata={"is_error": True, "error_type": "approval_not_approved"},
            )
        if record.is_expired:
            self.approval_store.expire(record)
            return ToolExecutionResult.denied(
                f"审批 {approval_id} 已过期，工具未执行。",
                metadata={"is_error": True, "error_type": "approval_expired"},
            )
        if record.tool_call_hash != tool_call_hash(call):
            return ToolExecutionResult.denied(
                f"审批 {approval_id} 的工具调用参数不匹配，工具未执行。",
                metadata={"is_error": True, "error_type": "approval_hash_mismatch"},
            )
        result = next(ctx)
        if result.status in {"completed", "denied"}:
            self.approval_store.consume(record)
        return result


def tool_call_hash(call: ToolCall) -> str:
    payload = {
        "id": call.id,
        "name": call.name,
        "arguments": _jsonable(call.arguments),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def render_approval_required(approval: ApprovalRecord, *, delivered: bool) -> str:
    delivery = "已发送审批请求" if delivered else "审批请求发送失败"
    reasons = "；".join(approval.risk_reasons) if approval.risk_reasons else "高风险工具调用"
    return "\n".join(
        [
            "工具调用需要人工审批。",
            f"approval_id={approval.id}",
            f"tool={approval.tool_name}",
            f"reason={reasons}",
            f"expires_at={approval.expires_at}",
            delivery,
            f"审批通过后回复 /approve {approval.id}；拒绝请回复 /reject {approval.id} 原因。",
        ]
    )


def render_rejected_observation(approval: ApprovalRecord) -> str:
    reason = approval.decision_reason or "未提供原因"
    return "\n".join(
        [
            "人工审批已拒绝，工具未执行。",
            f"approval_id={approval.id}",
            f"tool={approval.tool_name}",
            f"reason={reason}",
        ]
    )


def approval_record_to_json(record: ApprovalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "session_key": record.session_key,
        "session_source": record.session_source,
        "session_external_id": record.session_external_id,
        "tool_call_id": record.tool_call_id,
        "tool_name": record.tool_name,
        "arguments": _jsonable(record.arguments),
        "tool_call_hash": record.tool_call_hash,
        "risk_reasons": list(record.risk_reasons),
        "checkpoint_id": record.checkpoint_id,
        "status": record.status,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "decided_at": record.decided_at,
        "decision_reason": record.decision_reason,
    }


def approval_record_from_json(payload: Mapping[str, Any]) -> ApprovalRecord:
    status = str(payload["status"])
    if status not in {"pending", "approved", "rejected", "expired", "consumed"}:
        raise ValueError(f"Invalid approval status: {status}")
    return ApprovalRecord(
        id=str(payload["id"]),
        session_key=str(payload["session_key"]),
        session_source=str(payload["session_source"]),
        session_external_id=str(payload["session_external_id"]),
        tool_call_id=str(payload["tool_call_id"]),
        tool_name=str(payload["tool_name"]),
        arguments=_mapping(payload.get("arguments", {})),
        tool_call_hash=str(payload["tool_call_hash"]),
        risk_reasons=tuple(str(reason) for reason in payload.get("risk_reasons", [])),
        checkpoint_id=str(payload["checkpoint_id"]),
        status=cast(ApprovalStatus, status),
        created_at=str(payload["created_at"]),
        expires_at=str(payload["expires_at"]),
        decided_at=_optional_str(payload.get("decided_at")),
        decision_reason=_optional_str(payload.get("decision_reason")),
    )


def checkpoint_to_json(checkpoint: RunCheckpoint) -> dict[str, Any]:
    return {
        "id": checkpoint.id,
        "session_key": checkpoint.session_key,
        "mode": checkpoint.mode,
        "prompt": checkpoint.prompt,
        "step": checkpoint.step,
        "max_steps": checkpoint.max_steps,
        "phase": checkpoint.phase,
        "tool_policy": checkpoint.tool_policy,
        "provider": checkpoint.provider,
        "current_plan_todo_id": checkpoint.current_plan_todo_id,
        "current_step_had_tool_error": checkpoint.current_step_had_tool_error,
        "plan_required": checkpoint.plan_required,
        "visible_tool_names": list(checkpoint.visible_tool_names),
        "messages": [message_to_json(message) for message in checkpoint.messages],
        "pending_tool_calls": [tool_call_to_json(call) for call in checkpoint.pending_tool_calls],
        "pending_index": checkpoint.pending_index,
        "created_at": checkpoint.created_at,
    }


def checkpoint_from_json(payload: Mapping[str, Any]) -> RunCheckpoint:
    return RunCheckpoint(
        id=str(payload["id"]),
        session_key=str(payload["session_key"]),
        mode=str(payload["mode"]),
        prompt=str(payload["prompt"]),
        step=int(payload["step"]),
        max_steps=int(payload["max_steps"]),
        phase=str(payload["phase"]),
        tool_policy=str(payload["tool_policy"]),
        provider=str(payload["provider"]),
        current_plan_todo_id=_optional_str(payload.get("current_plan_todo_id")),
        current_step_had_tool_error=bool(payload["current_step_had_tool_error"]),
        plan_required=bool(payload["plan_required"]),
        visible_tool_names=tuple(str(name) for name in payload.get("visible_tool_names", [])),
        messages=tuple(message_from_json(message) for message in payload.get("messages", [])),
        pending_tool_calls=tuple(
            tool_call_from_json(call) for call in payload.get("pending_tool_calls", [])
        ),
        pending_index=int(payload["pending_index"]),
        created_at=str(payload["created_at"]),
    )


def message_to_json(message: Message) -> dict[str, Any]:
    return {
        "role": message.role.value,
        "content": message.content,
        "tool_calls": [tool_call_to_json(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
        "name": message.name,
        "metadata": _jsonable(message.metadata),
    }


def message_from_json(payload: Mapping[str, Any]) -> Message:
    from tiny_claw._internal.schema.message import Role

    return Message(
        role=Role(str(payload["role"])),
        content=str(payload.get("content", "")),
        tool_calls=tuple(tool_call_from_json(call) for call in payload.get("tool_calls", [])),
        tool_call_id=_optional_str(payload.get("tool_call_id")),
        name=_optional_str(payload.get("name")),
        metadata=_mapping(payload.get("metadata", {})),
    )


def tool_call_to_json(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": _jsonable(call.arguments)}


def tool_call_from_json(payload: Mapping[str, Any]) -> ToolCall:
    return ToolCall(
        id=str(payload["id"]),
        name=str(payload["name"]),
        arguments=_mapping(payload.get("arguments", {})),
    )


def _replace_approval(
    record: ApprovalRecord,
    *,
    status: ApprovalStatus,
    decided_at: str | None = None,
    decision_reason: str | None = None,
) -> ApprovalRecord:
    return ApprovalRecord(
        id=record.id,
        session_key=record.session_key,
        session_source=record.session_source,
        session_external_id=record.session_external_id,
        tool_call_id=record.tool_call_id,
        tool_name=record.tool_name,
        arguments=record.arguments,
        tool_call_hash=record.tool_call_hash,
        risk_reasons=record.risk_reasons,
        checkpoint_id=record.checkpoint_id,
        status=status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        decided_at=decided_at if decided_at is not None else record.decided_at,
        decision_reason=(
            decision_reason if decision_reason is not None else record.decision_reason
        ),
    )


def _jsonable(value: object) -> object:
    try:
        json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value)
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _metadata_str(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _requester_from_metadata(metadata: Mapping[str, Any]) -> ApprovalRequester:
    value = metadata.get("approval_requester")
    if hasattr(value, "request_approval"):
        return cast(ApprovalRequester, value)
    return NullApprovalRequester()


def _metadata_int(metadata: Mapping[str, Any], key: str, default: int) -> int:
    value = metadata.get(key)
    if isinstance(value, int):
        return value
    return default


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
