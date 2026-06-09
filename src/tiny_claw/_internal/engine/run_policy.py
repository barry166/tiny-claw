"""Mode and tool-policy rules for engine turns."""

from __future__ import annotations

from tiny_claw._internal.engine.run_types import RunMode, ToolPolicy
from tiny_claw._internal.provider.base import ToolChoice


def to_tool_choice(policy: ToolPolicy) -> ToolChoice:
    if policy is ToolPolicy.NONE:
        return ToolChoice.NONE
    return ToolChoice.AUTO


def phase_for_step(*, mode: RunMode, step: int, plan_required: bool = False) -> str:
    if mode is RunMode.THINK:
        return "think"
    if mode is RunMode.PLAN_ACT and plan_required and step == 1:
        return "plan"
    if mode is RunMode.PLAN_ACT:
        return "act"
    return "act"


def tool_policy_for_phase(phase: str) -> ToolPolicy:
    if phase in {"think", "plan"}:
        return ToolPolicy.NONE
    return ToolPolicy.AUTO
