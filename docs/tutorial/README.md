# tiny-claw 教程索引

本目录按 `tiny-claw` 的运行时边界整理技术教程。建议先读
[`00-开篇-从黑盒-agent-到可控-harness.md`](00-开篇-从黑盒-agent-到可控-harness.md)，再按下面六个部分逐步进入实现。

`tiny-claw` 的完整模块边界可以配合阅读 [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)。本目录中的文章按当前代码库已实现能力组织；发布到具体版本文档时，应以对应版本的代码和验证结果为准。

## 推荐阅读路径

如果你不是从头阅读，可以按目标选择路线：

- **新读者路线**：`00 -> 01 -> 02 -> 03 -> 04`，先建立整体架构和最小运行时。
- **工具与安全路线**：`04 -> 05 -> 06 -> 14 -> 16 -> 17 -> 18 -> 28`，集中理解工具、副作用、并发和审批。
- **生产化路线**：`10 -> 18 -> 19 -> 20 -> 21 -> 22 -> 29`，集中理解外部平台、审批恢复、测试和 tracing。
- **Subagent 路线**：`23 -> 24 -> 25 -> 26 -> 27 -> 29`，集中理解探索隔离、子会话和可观测性。

### 第一部分：基础运行时

目标：理解 `tiny-claw` 的最小可运行骨架，以及为什么入口、主循环和 Provider 必须分层。

- [`01-分层-python-智能体-cli-框架.md`](01-分层-python-智能体-cli-框架.md)：搭建 CLI、`Application` 和 `_internal` 边界。
- [`02-模型无关-react-主循环.md`](02-模型无关-react-主循环.md)：把 `MainLoop` 做成调度中心，而不是万能类。
- [`03-模型-provider-适配层.md`](03-模型-provider-适配层.md)：用 Provider 翻译厂商协议，避免 SDK 细节污染 engine。

### 第二部分：工具与安全边界

目标：讲清楚 Agent 从“会回答”到“能行动”后，工具系统如何控制副作用。

- [`04-受控工具系统.md`](04-受控工具系统.md)：工具体系总入口。
- [`05-安全局部编辑工具.md`](05-安全局部编辑工具.md)：安全局部编辑工具。
- [`06-多工具并发执行器.md`](06-多工具并发执行器.md)：只读工具并发与副作用工具顺序执行。
- [`14-edit-分层降级匹配管线.md`](14-edit-分层降级匹配管线.md)：`edit` 的匹配策略进阶。
- [`16-通用-tool-middleware-链式执行.md`](16-通用-tool-middleware-链式执行.md)：工具横切能力入口。
- [`17-运行时工具策略-allowlist-denylist.md`](17-运行时工具策略-allowlist-denylist.md)：运行时工具策略。
- [`18-高危工具调用人工审批-middleware.md`](18-高危工具调用人工审批-middleware.md)：高危工具审批拦截。
- [`28-tool-concurrency-boundaries.md`](28-tool-concurrency-boundaries.md)：工具并发边界复盘。

### 第三部分：上下文、记忆与计划

目标：说明模型“本轮看到什么”“跨轮记住什么”“长任务如何恢复”。

- [`07-技能感知上下文引擎.md`](07-技能感知上下文引擎.md)：`AGENTS.md`、skills 与工具权限收窄。
- [`08-会话隔离记忆设计.md`](08-会话隔离记忆设计.md)：CLI、Feishu 与后续 child session 的记忆隔离。
- [`09-可恢复计划模式.md`](09-可恢复计划模式.md)：`PLAN.md` / `TODO.md` 外部化计划。
- [`11-上下文压缩器.md`](11-上下文压缩器.md)：请求前临时压缩视图。
- [`12-工具错误-sop-兜底机制.md`](12-工具错误-sop-兜底机制.md)：把工具错误变成可恢复反馈。

### 第四部分：外部集成与审批恢复

目标：理解 Feishu 不是另一个 Agent runtime，而是复用 `Application` / `MainLoop` 的外部入口；审批也不是阻塞等待，而是 checkpoint 恢复。

- [`10-飞书事件服务.md`](10-飞书事件服务.md)：Feishu HTTP 事件如何复用 runtime。
- [`19-审批-checkpoint-暂停恢复.md`](19-审批-checkpoint-暂停恢复.md)：审批暂停恢复核心链路。
- [`20-飞书审批-adapter.md`](20-飞书审批-adapter.md)：Feishu 是 approval adapter，不是 tool。
- [`21-审批流程测试与验证.md`](21-审批流程测试与验证.md)：审批流程验收方法。
- [`22-mainloop-审批恢复重构.md`](22-mainloop-审批恢复重构.md)：审批恢复后的主循环职责拆分。

### 第五部分：Subagent 与可观测性

目标：讲复杂探索如何隔离，运行过程如何被观察、审计和复盘。

- [`23-explorer-subagent-runtime.md`](23-explorer-subagent-runtime.md)：Explorer Subagent 为什么只读、同步、上下文隔离。
- [`24-explore-tool-adapter.md`](24-explore-tool-adapter.md)：把 `explore` 封装成普通工具。
- [`25-subagent-session-memory-isolation.md`](25-subagent-session-memory-isolation.md)：child session / memory 隔离。
- [`26-subagent-observability.md`](26-subagent-observability.md)：Subagent 日志可观测性。
- [`27-openai-subagent-live-test.md`](27-openai-subagent-live-test.md)：真实 OpenAI E2E 验收。
- [`29-agent-tracing-json-decision-tree.md`](29-agent-tracing-json-decision-tree.md)：本地 tracing 与 JSON 决策树。

### 第六部分：测试与验收

目标：区分哪些行为靠稳定单元测试证明，哪些行为靠 live demo 补充证明。

- [`13-智能体-cli-测试策略.md`](13-智能体-cli-测试策略.md)：测试体系总论。
- [`15-真实-provider-edit-demo.md`](15-真实-provider-edit-demo.md)：真实 Provider 编辑流程 demo。
- [`21-审批流程测试与验证.md`](21-审批流程测试与验证.md)：审批测试。
- [`27-openai-subagent-live-test.md`](27-openai-subagent-live-test.md)：Subagent live test。

## 完整目录

| 推荐文件名 | 文章标题 | 所属部分 | 读者对象 |
| --- | --- | --- | --- |
| `00-开篇-从黑盒-agent-到可控-harness.md` | 开篇词：从黑盒 Agent 到可控 Harness | 总览 | 所有读者 |
| `01-分层-python-智能体-cli-框架.md` | 从零搭建一个分层 Python Agent CLI 框架 | 基础运行时 | Python CLI 开发者 / Agent 框架开发者 |
| `02-模型无关-react-主循环.md` | 把 Agent 主循环从模型 SDK 和工具实现中解耦 | 基础运行时 | Agent 框架开发者 |
| `03-模型-provider-适配层.md` | 为 Agent CLI 设计可替换的 Provider 适配层 | 基础运行时 | Agent 框架开发者 / 项目使用者 |
| `04-受控工具系统.md` | AI Agent 工具系统的权限边界设计 | 工具与安全边界 | Agent 框架开发者 / 工具系统维护者 |
| `05-安全局部编辑工具.md` | 为 AI Agent 实现一个安全的局部文件编辑工具 | 工具与安全边界 | 工具系统维护者 / 后续维护者 |
| `06-多工具并发执行器.md` | 让 Agent 工具调用支持安全并发 | 工具与安全边界 | Agent 框架开发者 / 测试工程师 |
| `07-技能感知上下文引擎.md` | 给 Agent 加一个可扩展的 Skill 上下文系统 | 上下文、记忆与计划 | 上下文工程开发者 / 后续维护者 |
| `08-会话隔离记忆设计.md` | 让 Agent 记忆按 Session 隔离 | 上下文、记忆与计划 | Agent 框架开发者 / 外部集成维护者 |
| `09-可恢复计划模式.md` | 给 AI Agent 加一个真正可恢复的 Plan Mode | 上下文、记忆与计划 | Agent 框架开发者 / CLI 开发者 |
| `10-飞书事件服务.md` | 让 Feishu 回调复用真实 Agent 运行时 | 外部集成与审批恢复 | 外部集成维护者 / 项目使用者 |
| `11-上下文压缩器.md` | 防止工具输出撑爆上下文：Agent Context Compactor 设计 | 上下文、记忆与计划 | 上下文工程开发者 / 后续维护者 |
| `12-工具错误-sop-兜底机制.md` | 让 Agent 看懂工具错误：从原始报错到 SOP 自恢复 | 上下文、记忆与计划 | 工具系统维护者 / 测试工程师 |
| `13-智能体-cli-测试策略.md` | 如何测试一个带工具和持久状态的 Agent CLI | 测试与验收 | 测试工程师 / 后续维护者 |
| `14-edit-分层降级匹配管线.md` | 从精确匹配到缩进归一：Agent 文件编辑的匹配策略设计 | 工具与安全边界 | 工具系统维护者 / Agent 框架开发者 |
| `15-真实-provider-edit-demo.md` | 用真实模型验收 Agent 的文件编辑工具 | 测试与验收 | 项目使用者 / 后续维护者 |
| `16-通用-tool-middleware-链式执行.md` | 让 ToolRegistry 支持通用 Middleware 链式执行 | 工具与安全边界 | Agent 框架开发者 / 工具系统维护者 |
| `17-运行时工具策略-allowlist-denylist.md` | 用 Allowlist 和 Denylist 控制运行时工具策略 | 工具与安全边界 | 项目使用者 / 工具系统维护者 |
| `18-高危工具调用人工审批-middleware.md` | 用 HumanApprovalMiddleware 拦截高危工具调用 | 工具与安全边界 | Agent 框架开发者 / 安全策略维护者 |
| `19-审批-checkpoint-暂停恢复.md` | 用 Checkpoint 实现审批后的暂停与恢复 | 外部集成与审批恢复 | Agent 主循环开发者 / 状态管理维护者 |
| `20-飞书审批-adapter.md` | 把飞书接成审批 Adapter，而不是工具 | 外部集成与审批恢复 | 外部集成维护者 / Agent 平台开发者 |
| `21-审批流程测试与验证.md` | 如何测试高危工具审批流程 | 外部集成与审批恢复 / 测试与验收 | 测试工程师 / 项目使用者 |
| `22-mainloop-审批恢复重构.md` | 拆分 MainLoop 中的审批恢复职责 | 外部集成与审批恢复 | 后续维护者 / Agent 主循环开发者 |
| `23-explorer-subagent-runtime.md` | 为 AI Agent 设计一个只读 Explorer Subagent | Subagent 与可观测性 | AI Agent 框架开发者 / 后续维护者 |
| `24-explore-tool-adapter.md` | 把 Subagent 封装成一个普通 Tool：`explore` 的接入设计 | Subagent 与可观测性 | 工具系统维护者 / Python CLI 开发者 |
| `25-subagent-session-memory-isolation.md` | AI Agent 的子会话隔离：让探索结果回流而不是上下文泄洪 | Subagent 与可观测性 | AI Agent 框架开发者 / 后续维护者 |
| `26-subagent-observability.md` | 给 Subagent 加上可读日志：从启动、结束到 child tool 标记 | Subagent 与可观测性 | 后续维护者 / 测试工程师 |
| `27-openai-subagent-live-test.md` | 如何为 AI Agent Subagent 写一个真实 OpenAI E2E 测试 | Subagent 与可观测性 / 测试与验收 | 测试工程师 / AI Agent 框架开发者 |
| `28-tool-concurrency-boundaries.md` | Tiny Claw 的工具并发模型：为什么 read 可以并发，explore 暂不并发 | 工具与安全边界 | 架构设计读者 / 后续维护者 |
| `29-agent-tracing-json-decision-tree.md` | 从黑盒到决策树：为 Agent 实现轻量级 Tracing | Subagent 与可观测性 | AI Agent 框架开发者 / Python CLI 开发者 / 后续维护者 |
