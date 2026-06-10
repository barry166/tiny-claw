"""Explorer subagent tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.subagent import (
    SUBAGENT_DEFAULT_MAX_STEPS,
    SUBAGENT_MAX_STEPS_LIMIT,
    SubagentRunner,
)
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin._common import positive_int


@dataclass(frozen=True)
class ExplorerSubagentTool:
    runner: SubagentRunner

    @property
    def name(self) -> str:
        return "explore"

    @property
    def description(self) -> str:
        return (
            "派出一个只读 Explorer Subagent 执行深度探索。适合大量代码阅读、跨文件查找逻辑、"
            "日志定位；子智能体在独立上下文中探索，最后只返回精炼纯文本报告。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "需要 Explorer Subagent 调查的具体任务。",
                },
                "max_steps": {
                    "type": "integer",
                    "description": (
                        "子智能体最大模型轮次。默认 "
                        f"{SUBAGENT_DEFAULT_MAX_STEPS}，上限 {SUBAGENT_MAX_STEPS_LIMIT}。"
                    ),
                    "default": SUBAGENT_DEFAULT_MAX_STEPS,
                    "minimum": 1,
                    "maximum": SUBAGENT_MAX_STEPS_LIMIT,
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        task = str(input.arguments.get("task", "")).strip()
        if not task:
            raise ToolError("explore tool requires a non-empty 'task' field")
        if input.session is None:
            raise ToolError("explore tool requires a runtime session")

        max_steps = positive_int(
            input.arguments.get("max_steps", SUBAGENT_DEFAULT_MAX_STEPS),
            field_name="max_steps",
            tool_name=self.name,
        )
        try:
            result = self.runner.run_explorer(
                task=task,
                parent_session=input.session,
                max_steps=max_steps,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        return ToolOutput(content=result.text)
