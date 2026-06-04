# tiny-claw 智能体上下文

本文件是 Codex 及其他 AI 编码智能体读取的项目上下文。修改本仓库代码前，先阅读这里的约定。

## 项目目标

`tiny-claw` 是一个分层式 Python CLI 工业级框架骨架，借鉴 Go 项目中 `cmd/` 和
`internal/` 的边界思想。

- 面向用户的终端命令名：`tiny-claw`
- Python 可导入包名：`tiny_claw`
- 第一版定位：CLI 优先；`serve` 子命令提供统一 HTTP 事件服务入口，用于飞书等外部平台回调。

框架需要提供清晰的命令入口、应用装配层，以及面向后续扩展的内部模块：`engine`、
`provider`、`context`、`tools`、`memory`、`integrations`。

## 架构分层

- `src/tiny_claw/cli.py`：只做 CLI 参数解析和命令分发，不写业务逻辑。
- `src/tiny_claw/__main__.py`：支持 `python -m tiny_claw`，内部委托给
  `tiny_claw.cli.main`。
- `src/tiny_claw/_internal/app.py`：应用装配层，负责创建 settings、provider、memory、
  tools、engine。
- `src/tiny_claw/_internal/engine/`：主循环编排。
- `src/tiny_claw/_internal/provider/`：大模型 provider 抽象和具体厂商适配。
- `src/tiny_claw/_internal/context/`：Prompt / 上下文构建和 token 辅助逻辑。
- `src/tiny_claw/_internal/tools/`：工具注册表、中间件和内置工具骨架。
- `src/tiny_claw/_internal/memory/`：基于文件系统的记忆与状态存储。
- `src/tiny_claw/_internal/integrations/`：外部系统集成，例如飞书。

`_internal` 视为私有实现边界。外部使用应优先通过 `tiny-claw` 命令、
`python -m tiny_claw`，或明确暴露的公开 API。

## 运行入口

常用命令：

```bash
uv run tiny-claw --help
uv run tiny-claw health
uv run tiny-claw run "hello"
uv run tiny-claw run --mode think "先分析并制定计划"
uv run tiny-claw run --mode plan-act "先规划再执行"
uv run tiny-claw serve --host 0.0.0.0 --port 8000
uv run python -m tiny_claw --help
```

打包入口配置在 `pyproject.toml`：

```toml
[project.scripts]
tiny-claw = "tiny_claw.cli:main"
```

统一 HTTP 服务：

```bash
FEISHU_APP_ID=cli_xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_VERIFICATION_TOKEN=xxx \
FEISHU_ENCRYPT_KEY=xxx \
uv run tiny-claw serve --host 0.0.0.0 --port 8000
```

- `GET /health`：服务健康检查。
- `POST /api/events/feishu`：飞书事件回调入口，默认路径可用 `FEISHU_EVENT_PATH` 或
  `--feishu-path` 覆盖。
- 本地飞书回调测试需要公网 HTTPS，例如 `ngrok http 8000`，飞书后台 Request URL 配置为
  `https://<public-host>/api/events/feishu`。
- 未配置飞书 Encrypt Key 或 Verification Token 时，可省略对应环境变量；如果飞书后台配置了，
  本地环境必须保持一致。
- 真实模型回复时继续使用统一 `serve` 入口，并叠加 provider 配置，例如
  `TINY_CLAW_PROVIDER=openai OPENAI_API_KEY=... TINY_CLAW_ENABLED_TOOLS=read`。

## 开发约定

- CLI 使用标准库 `argparse`，除非用户明确要求改用 Typer 或 Click。
- 未经用户明确要求，不新增运行时依赖。
- 真实 SDK / 厂商接入必须收敛在 `provider/` 适配层后面。
- 工具能力默认禁用，除非调用方显式启用。
- `run --mode think` 用于先分析 / 先计划场景，主循环不会向模型暴露工具定义，也会阻止意外工具调用。
- `run --mode plan-act` 会先隐藏工具完成规划，再自动进入 ReAct 执行阶段；规划轮计入 `--max-steps`。
- `run --mode act` 是默认 ReAct 执行模式，会按工具策略向 provider 传递工具定义。
- `engine` 相关测试应优先使用依赖注入，方便注入 fake provider、fake memory、fake
  tools。
- 源码保持 `src/` layout，测试保持在 `tests/`。

## 验证命令

完成代码变更前，至少运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

如果变更涉及 CLI，也要运行：

```bash
uv run tiny-claw --help
uv run tiny-claw serve --help
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw health
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw run "hello tiny claw"
uv run python -m tiny_claw --help
```

手动冒烟测试结束后，删除 `.tmp-state/`。

## 配置项

- `TINY_CLAW_LOG_LEVEL`：`DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`，默认
  `INFO`。
- `TINY_CLAW_PROVIDER`：provider 名称，默认 `echo`。
- `TINY_CLAW_PROVIDER` 支持 `echo`、`openai`、`claude`、`anthropic`。
- `TINY_CLAW_MODEL`：模型名称；OpenAI 默认 `gpt-5.4`，Claude 默认
  `claude-sonnet-4-20250514`，其他 provider 默认等于 provider 名称。
- `TINY_CLAW_MAX_TOKENS`：模型最大输出 token，默认 `1024`。
- `TINY_CLAW_STATE_DIR`：记忆 / 状态目录，默认 `~/.tiny-claw`。
- `TINY_CLAW_SERVER_HOST`：统一 HTTP 服务监听 host，默认 `0.0.0.0`。
- `TINY_CLAW_SERVER_PORT`：统一 HTTP 服务监听端口，默认 `8000`。
- `OPENAI_API_KEY` 或 `OPENAI_KEY`：`openai` provider 必需。
- `OPENAI_BASE_URL`：可选 OpenAI-compatible API base URL。
- `ANTHROPIC_API_KEY` 或 `CLAUDE_KEY`：`claude` / `anthropic` provider 必需。
- `FEISHU_APP_ID` 或 `LARK_APP_ID`：飞书事件回调 endpoint 必需。
- `FEISHU_APP_SECRET` 或 `LARK_APP_SECRET`：飞书事件回调 endpoint 必需。
- `FEISHU_VERIFICATION_TOKEN`：飞书事件订阅 verification token，可选。
- `FEISHU_ENCRYPT_KEY`：飞书事件订阅 encrypt key，可选。
- `FEISHU_EVENT_PATH`：飞书事件回调路径，默认 `/api/events/feishu`。

CLI 会读取当前执行目录下的 `.env` 文件，真实环境变量优先级高于 `.env`。
`.env` 包含密钥，必须保持在 git ignore 中。

## 当前 Provider 策略

默认 `echo` provider 保持无 API key 可运行。`openai` 和 `claude` provider 使用官方 SDK，
不同厂商的请求 / 响应结构转换应收敛在 `provider/` 适配层，`engine` 不应依赖具体厂商。
