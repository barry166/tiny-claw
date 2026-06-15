# 用 Checkpoint 实现审批后的暂停与恢复

## 本节目标

> 导读：本篇进入第四部分「外部集成与审批恢复」的核心：审批不能阻塞等待，必须用 checkpoint 保存可恢复的运行现场。

本节要实现的是审批后的 checkpoint 暂停与恢复：把原始 messages、pending tool call 和运行参数持久化，让人工决策后可以安全继续。

完成这一节后，你会理解为什么审批不能阻塞等待，以及恢复路径如何做到 fail closed。

## 摘要

本文要说明 `tiny-claw` 如何在高危工具调用被拦截后，使用持久化 approval 和 checkpoint 恢复原始运行。这个模块适合 Agent 主循环开发者、状态管理维护者和需要实现人工审批恢复机制的读者。读完后，你会理解为什么不能阻塞进程等待审批、checkpoint 保存了哪些信息，以及恢复时如何做到 fail closed。

## 背景与问题

高危工具审批的难点不在于“发一条审批消息”，而在于审批之后系统还能安全、准确地继续执行。

直接挂起进程等待人工确认有几个问题：

- HTTP 请求或 Feishu 事件处理不能长时间占住线程。
- 进程重启后审批状态会丢失。
- 多个用户、多个 chat、多个 session 的审批容易混淆。
- 人工通过后必须执行原始 tool call，而不是重新让模型生成一个可能变化的调用。

因此，审批流程需要“暂停 + 持久化 + 恢复”，而不是同步阻塞等待。

## 设计目标

- **非阻塞**：高危调用立即暂停当前 run，不占住请求线程。
- **可恢复**：恢复时能够拿回原始 messages、pending tool call 和运行参数。
- **原始调用冻结**：审批通过后执行被审批的原始 tool call。
- **会话隔离**：approval 和 checkpoint 都绑定 session key。
- **失败关闭**：跨 session、过期、重复审批、hash 不匹配都拒绝执行。
- **继续对话**：拒绝审批也要作为 tool observation 返回给模型，让模型给出后续回应。

## 整体方案

审批暂停恢复流程如下：

```mermaid
sequenceDiagram
  participant Loop as MainLoop
  participant Middleware as HumanApprovalMiddleware
  participant Store as File stores
  participant User as Human
  participant App as Application
  participant Resume as ApprovalResumeRunner
  participant Tool as Tool
  participant Provider as Provider

  Loop->>Middleware: tool call + RunCheckpointDraft
  Middleware->>Store: write checkpoint
  Middleware->>Store: write approval
  Middleware-->>Loop: suspended
  Loop-->>User: approval_required
  User->>App: approve/reject approval_id
  App->>Store: validate approval
  App->>Resume: resume approved/rejected
  Resume->>Store: read checkpoint
  Resume->>Tool: execute original pending tool call
  Resume->>Provider: continue with tool observation
```

状态目录形态：

```text
state_dir/
  sessions/
    <session-key>/
      approvals/
        <approval-id>.json
      checkpoints/
        <checkpoint-id>.json
```

## 核心实现

关键文件：

- `src/tiny_claw/_internal/approval.py`
- `src/tiny_claw/_internal/engine/approval_resume.py`
- `src/tiny_claw/_internal/engine/main_loop.py`
- `src/tiny_claw/_internal/app.py`
- `tests/test_engine.py`

approval 记录由 `ApprovalRecord` 表示，包含：

- `id`
- `session_key`
- `session_source`
- `session_external_id`
- `tool_call_id`
- `tool_name`
- `arguments`
- `tool_call_hash`
- `risk_reasons`
- `checkpoint_id`
- `status`
- `created_at`
- `expires_at`

checkpoint 由 `RunCheckpoint` 表示，包含恢复主循环需要的上下文：

- 运行模式、prompt、step、max_steps、phase、tool_policy、provider
- 当前 plan-act TODO 状态
- 可见工具名
- 已有 messages
- pending tool calls
- pending index

暂停前，`MainLoop` 创建 `RunCheckpointDraft`，并通过 `context_metadata` 交给工具执行器：

```python
context_metadata={
    CHECKPOINT_DRAFT_METADATA_KEY: draft,
    "approval_requester": resolved_channel,
}
```

`HumanApprovalMiddleware` 将 draft 落成真实 checkpoint，再创建 approval。

恢复入口在应用层：

```python
app.resume_approval(
    approval_id=...,
    decision="approve",
    session=session,
)
```

应用层先校验：

- approval 是否存在。
- approval 是否属于当前 session。
- approval 是否仍是 `pending`。
- approval 是否过期。

通过后再进入 `MainLoop.resume_approved_approval(...)` 或 `MainLoop.resume_rejected_approval(...)`。

审批通过时，`ApprovalResumeRunner` 读取 checkpoint，并执行原始 pending tool call：

```python
batch = tool_executor.run_tool_batch(
    (pending_call,),
    session=session,
    workdir=session.workdir,
    context_metadata={APPROVAL_METADATA_KEY: approval.id},
)
```

这里的 `APPROVAL_METADATA_KEY` 会让 `HumanApprovalMiddleware` 进入已审批执行路径。它还会校验 tool call hash，确保恢复时参数没有被替换。

审批拒绝时，不执行真实工具，而是构造一个 rejected tool observation，再继续让 provider 生成最终回复。

## 使用方式

普通用户通过 Feishu 命令触发恢复：

```text
/approve <approval-id>
/reject <approval-id> 原因
```

内部应用代码可以直接调用：

```python
result = app.resume_approval(
    approval_id=approval_id,
    decision="approve",
    session=session,
)
```

审批暂停后的 `RunResult` 会带上：

```python
RunResult(
    stop_reason="approval_required",
    approval_id="...",
    checkpoint_id="...",
)
```

可以通过状态目录查看持久化记录：

```bash
find "$TINY_CLAW_STATE_DIR/sessions" -path '*/approvals/*.json' -print
find "$TINY_CLAW_STATE_DIR/sessions" -path '*/checkpoints/*.json' -print
```

注意：当前项目没有实现独立 CLI 子命令来 approve/reject。已落地的用户侧恢复入口是 Feishu 文本命令；程序内部入口是 `Application.resume_approval(...)`。

## 测试与验证

审批恢复测试集中在 `tests/test_engine.py`：

```bash
uv run pytest tests/test_engine.py
```

重点测试：

- `test_main_loop_suspends_high_risk_tool_for_approval`
- `test_main_loop_resumes_approved_high_risk_tool`
- `test_main_loop_consumes_approval_after_approved_tool_error`
- `test_main_loop_resumes_rejected_high_risk_tool_as_observation`

Feishu 命令路由测试：

```bash
uv run pytest tests/test_feishu_integration.py
```

完整验证：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## 设计取舍与注意事项

暂停恢复的核心取舍是“不阻塞进程”。这让 HTTP 服务、Feishu 回调和 CLI 运行都能用同一套机制处理审批，而不是为每个入口写一种等待逻辑。

审批通过后执行的是 checkpoint 中冻结的原始 tool call，不重新问模型。这一点降低了参数漂移风险。恢复后才把工具 observation 交给 provider，让模型继续解释结果或提出下一步。

审批记录被消费后不能重复使用。即使工具执行返回错误，审批也会被标记为 consumed，避免用户或平台重放同一个 approval id 导致重复副作用。

当前实现会校验 session 和 tool call hash。更细粒度的 chat 用户身份校验、审批人白名单、审计日志导出属于待确认的后续能力。

## 总结

- 人工审批应该暂停并持久化，而不是阻塞等待。
- approval 保存决策状态，checkpoint 保存恢复主循环所需上下文。
- 审批通过后执行原始 frozen tool call。
- 审批拒绝后注入 rejected observation，让模型继续回应。
- 恢复路径坚持 fail closed，防止跨 session、过期或重放执行。

按审批专题继续阅读：[20：Feishu 审批 adapter](20-飞书审批-adapter.md) 会把通用审批流程接到真实聊天平台。
