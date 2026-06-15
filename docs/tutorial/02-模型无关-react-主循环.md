# 把 Agent 主循环从模型 SDK 和工具实现中解耦

## 本节目标

> 导读：本篇属于第一部分「基础运行时」，聚焦控制流核心：让 `MainLoop` 做调度中心，而不是模型 SDK、工具实现和状态管理的混合体。

本节要实现的是 `tiny-claw` 的 Agent 主循环：一个可以接收用户请求、构建上下文、调用 Provider、执行工具调用、追加 observation，并在多轮 ReAct 流程中返回最终结果的编排核心。

完成这一节后，系统会具备下面这些能力：

- `tiny-claw run "..."` 可以进入稳定的多轮 Agent 主循环。
- 主循环可以根据 `act`、`think`、`plan`、`plan-act` 切换工具策略。
- 模型返回 tool calls 时，主循环能把它们交给 `ToolExecutor`，再把工具结果送回下一轮模型请求。
- OpenAI、Claude、Echo 或 FakeProvider 都可以接入同一个主循环，而不需要改控制流。

这一节的关键目标是把 `MainLoop` 做成“调度中心”，而不是模型 SDK、工具实现和状态管理的混合体。

## 摘要

Agent 主循环最容易膨胀成系统里的“万能类”：既懂模型 SDK，又懂工具实现，还负责保存状态。`tiny-claw` 刻意把 `MainLoop` 限定为编排层：它只决定模型本轮看到什么、能调用什么、工具结果如何进入下一轮。本文介绍 `MainLoop` 如何只依赖内部协议和抽象接口，而不直接绑定 OpenAI、Claude 或具体工具实现。

## 背景与问题

一个 ReAct Agent 至少要处理三类对象：模型消息、工具定义、工具调用结果。最直接的实现方式是让主循环直接拼 OpenAI payload、读取工具列表、执行工具函数，再把结果拼回模型请求。

这种写法的问题很快会显现：

- 接入 Claude 时，需要重写主循环里的消息转换。
- 测试时只能调用真实模型，难以构造确定性的 tool calls。
- 工具实现和工具调度混在一起，难以做并发、错误兜底和权限过滤。
- 新增 `plan`、`think`、`plan-act` 运行模式时，控制流会和厂商 SDK 细节缠在一起。

`tiny-claw` 的做法是把主循环变成 provider-neutral、tool-neutral 的编排层。

## 设计目标

- **Provider-neutral**：主循环只认识 `LLMProvider` 协议和内部 schema。
- **Tool-neutral**：主循环不执行具体工具逻辑，只调用 `ToolExecutor`。
- **模式清晰**：`act`、`think`、`plan`、`plan-act` 有明确工具策略。
- **可测试**：可以用 FakeProvider 构造稳定多轮响应。
- **可观测**：每轮请求、响应、工具调用和停止原因都有日志入口。
- **可恢复**：会话记忆和 plan 文件由 session 层提供，不硬编码在主循环里。

## 整体方案

`MainLoop.run()` 的输入是一次用户请求、运行模式、最大轮数和 `SessionRef`。它先通过上下文层生成本轮消息，再按模式决定是否暴露工具。模型返回 tool calls 时，主循环交给 `ToolExecutor` 执行，并把 observation 作为 `Role.TOOL` 消息追加回原始消息列表。

```mermaid
sequenceDiagram
  participant User
  participant Loop as MainLoop
  participant Context as ContextBuilder
  participant Provider as LLMProvider
  participant Executor as ToolExecutor
  participant Memory as SessionMemory

  User->>Loop: prompt + mode + session
  Loop->>Memory: read_recent()
  Loop->>Context: build(prompt, memories, workdir)
  Loop->>Provider: complete(LLMRequest)
  Provider-->>Loop: assistant message
  alt has tool calls and tools allowed
    Loop->>Executor: run_tool_calls()
    Executor-->>Loop: tool observations
    Loop->>Provider: complete(messages + observations)
  else final answer
    Loop->>Memory: append last_prompt / last_response
  end
```

## 核心实现

核心文件是 `src/tiny_claw/_internal/engine/main_loop.py`。

运行模式定义为：

```python
class RunMode(StrEnum):
    ACT = "act"
    PLAN = "plan"
    THINK = "think"
    PLAN_ACT = "plan-act"
```

工具策略只区分两种：

```python
class ToolPolicy(StrEnum):
    NONE = "none"
    AUTO = "auto"
```

`MainLoop` 的依赖都是抽象或项目内部协议：

```python
@dataclass(frozen=True)
class MainLoop:
    provider: LLMProvider
    context_builder: ContextBuilder
    context_compactor: ContextCompactor
    memory: SessionMemoryStore
    tools: ToolRegistry
```

在每轮模型请求前，主循环会基于当前 phase 选择是否暴露工具：

```python
request_tool_definitions = (
    registered_tool_definitions if tool_policy is ToolPolicy.AUTO else ()
)
```

Provider 请求使用项目内部 `LLMRequest`，不是厂商 SDK payload：

```python
response = self.provider.complete(
    LLMRequest(
        messages=compaction.messages,
        tools=request_tool_definitions,
        max_steps=max_steps,
        tool_choice=_to_tool_choice(tool_policy),
    )
)
```

这让 OpenAI、Claude、Echo 的差异被限制在 `provider/` 目录中。

## 使用方式

用户通常通过 CLI 触发主循环：

```bash
tiny-claw run "解释当前项目结构"
tiny-claw run --mode think "先分析问题，不要执行工具"
tiny-claw run --mode plan "为这个功能生成计划"
tiny-claw run --mode plan-act "按照计划继续执行"
```

工具暴露由配置控制：

```bash
TINY_CLAW_ENABLED_TOOLS=read,edit tiny-claw run "修改 README 中的一段文字"
```

如果不显式启用写类工具，默认只启用 `read`。

## 测试与验证

主循环适合用 FakeProvider 测试。测试可以精确控制模型每一轮返回什么：

```text
FakeProvider -> assistant tool_call(read)
ToolExecutor -> Role.TOOL observation
FakeProvider -> assistant final answer
```

推荐验证命令：

```bash
uv run pytest tests/test_engine.py
uv run pytest tests/test_tool_executor.py
uv run mypy src
```

全量验证：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## 设计取舍与注意事项

`MainLoop` 最重要的取舍，是拒绝成为“什么都知道”的对象。它不读取 `.env`，因为配置来源属于 `Settings` 和 `app.py`；它不直接执行工具，因为工具并发、错误兜底和 channel 通知属于 `ToolExecutor`；它也不理解 OpenAI 或 Claude 的 SDK 类型，因为厂商翻译属于 Provider。

运行模式也是边界的一部分。`think` 和 `plan` 阶段隐藏工具定义，不只是为了省 token，而是为了保证“只分析”和“只规划”的语义不会被模型绕过。如果模型仍然返回 tool calls，主循环会用工具策略阻止执行。

上下文压缩同样保持在请求视图层：`ContextCompactor` 只影响发给 provider 的临时 messages，不修改主循环里的原始历史。最后，`max_steps` 是硬边界，避免模型和工具在异常情况下无限循环。

## 总结

- `MainLoop` 是编排层，不是 Provider 适配层，也不是工具实现层。
- 内部 schema 让模型厂商差异保持在 provider 目录里。
- FakeProvider 让多轮 Agent 行为可以稳定测试。
- 运行模式和工具策略分离，使 `act`、`think`、`plan`、`plan-act` 的边界更清楚。

按编号继续阅读：[03：Provider 适配层](03-模型-provider-适配层.md) 会把模型厂商差异收敛到主循环之外。
