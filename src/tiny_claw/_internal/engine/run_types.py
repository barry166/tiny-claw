"""Shared run lifecycle types for the engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

STOP_REASON_FINAL = "final"
STOP_REASON_MAX_STEPS_EXHAUSTED = "max_steps_exhausted"
STOP_REASON_TOOL_POLICY_BLOCKED = "tool_policy_blocked"
STOP_REASON_APPROVAL_REQUIRED = "approval_required"
STOP_REASON_APPROVAL_RESUME_FAILED = "approval_resume_failed"


class ToolPolicy(StrEnum):
    NONE = "none"
    AUTO = "auto"


class RunMode(StrEnum):
    ACT = "act"
    PLAN = "plan"
    THINK = "think"
    PLAN_ACT = "plan-act"


@dataclass(frozen=True)
class RunResult:
    text: str
    provider: str
    steps: int
    max_steps: int
    workdir: Path
    stop_reason: str
    mode: RunMode = RunMode.ACT
    tool_policy: ToolPolicy = ToolPolicy.AUTO
    plan: str | None = None
    approval_id: str | None = None
    checkpoint_id: str | None = None
    trace_id: str | None = None
    trace_path: Path | None = None
