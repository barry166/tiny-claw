# tiny-claw 教程索引

本目录按模块边界整理 `tiny-claw` 的技术文章。推荐从基础架构开始阅读，再进入 Provider、工具、上下文、状态管理、外部集成和测试体系。

| 推荐文件名 | 文章标题 | 对应模块 | 读者对象 |
| --- | --- | --- | --- |
| `01-分层-python-智能体-cli-框架.md` | 从零搭建一个分层 Python Agent CLI 框架 | 分层 Python Agent CLI 框架 | Python CLI 开发者 / Agent 框架开发者 |
| `02-模型无关-react-主循环.md` | 把 Agent 主循环从模型 SDK 和工具实现中解耦 | 模型无关 ReAct 主循环 | Agent 框架开发者 |
| `03-模型-provider-适配层.md` | 为 Agent CLI 设计可替换的 Provider 适配层 | OpenAI / Claude / Echo Provider 适配层 | Agent 框架开发者 / 项目使用者 |
| `04-受控工具系统.md` | AI Agent 工具系统的权限边界设计 | 受控工具系统与显式工具启用 | Agent 框架开发者 / 工具系统维护者 |
| `05-安全局部编辑工具.md` | 为 AI Agent 实现一个安全的局部文件编辑工具 | 安全局部编辑工具 `EditTool` | 工具系统维护者 / 后续维护者 |
| `06-多工具并发执行器.md` | 让 Agent 工具调用支持安全并发 | 多工具同步调用与并发执行 | Agent 框架开发者 / 测试工程师 |
| `07-技能感知上下文引擎.md` | 给 Agent 加一个可扩展的 Skill 上下文系统 | Skill-aware Context 引擎 | 上下文工程开发者 / 后续维护者 |
| `08-会话隔离记忆设计.md` | 让 Agent 记忆按 Session 隔离 | Session 隔离记忆 | Agent 框架开发者 / 外部集成维护者 |
| `09-可恢复计划模式.md` | 给 AI Agent 加一个真正可恢复的 Plan Mode | 持久化 Plan Mode 与 `plan-act` | Agent 框架开发者 / CLI 开发者 |
| `10-飞书事件服务.md` | 让 Feishu 回调复用真实 Agent 运行时 | Feishu HTTP 事件服务与统一 Integration App | 外部集成维护者 / 项目使用者 |
| `11-上下文压缩器.md` | 防止工具输出撑爆上下文：Agent Context Compactor 设计 | Context Compactor 上下文压缩 | 上下文工程开发者 / 后续维护者 |
| `12-工具错误-sop-兜底机制.md` | 让 Agent 看懂工具错误：从原始报错到 SOP 自恢复 | 工具错误 SOP 兜底 | 工具系统维护者 / 测试工程师 |
| `13-智能体-cli-测试策略.md` | 如何测试一个带工具和持久状态的 Agent CLI | Agent 工程化测试体系 | 测试工程师 / 后续维护者 |
| `14-edit-分层降级匹配管线.md` | 从精确匹配到缩进归一：Agent 文件编辑的匹配策略设计 | `edit` 分层降级匹配管线 | 工具系统维护者 / Agent 框架开发者 |
| `15-真实-provider-edit-demo.md` | 用真实模型验收 Agent 的文件编辑工具 | 真实 Provider 编辑流程 Demo | 项目使用者 / 后续维护者 |

> 说明：这些教程按当前代码库中的已实现模块组织。发布到具体版本文档时，应以对应版本的代码和测试结果为准。
