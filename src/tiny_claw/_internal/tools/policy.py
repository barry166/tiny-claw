"""Runtime policy middleware for tool execution."""

from __future__ import annotations

from dataclasses import dataclass

from tiny_claw._internal.tools.middleware import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolNext,
)


@dataclass(frozen=True)
class ToolPolicyMiddleware:
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()

    def __call__(self, ctx: ToolExecutionContext, next: ToolNext) -> ToolExecutionResult:
        if ctx.tool_name in self.denylist:
            return ToolExecutionResult.denied(
                f"工具调用被运行时策略拒绝：{ctx.tool_name} 在 denylist 中。",
                metadata={
                    "is_error": True,
                    "error_type": "tool_policy_denied",
                    "tool_policy": "denylist",
                },
            )
        if self.allowlist and ctx.tool_name not in self.allowlist:
            return ToolExecutionResult.denied(
                f"工具调用被运行时策略拒绝：{ctx.tool_name} 不在 allowlist 中。",
                metadata={
                    "is_error": True,
                    "error_type": "tool_policy_denied",
                    "tool_policy": "allowlist",
                },
            )
        return next(ctx)
