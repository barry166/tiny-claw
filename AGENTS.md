# tiny-claw 智能体上下文

本文件是 Codex 及其他 AI 编码智能体读取的项目上下文。修改本仓库代码前，先阅读这里的约定。

## 项目目标

`tiny-claw` 是一个分层式 Python CLI 工业级框架骨架，借鉴 Go 项目中 `cmd/` 和
`internal/` 的边界思想。

- 面向用户的终端命令名：`tiny-claw`
- Python 可导入包名：`tiny_claw`
- 第一版定位：CLI 优先，不直接做 Web API

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
uv run python -m tiny_claw --help
```

打包入口配置在 `pyproject.toml`：

```toml
[project.scripts]
tiny-claw = "tiny_claw.cli:main"
```

## 开发约定

- CLI 使用标准库 `argparse`，除非用户明确要求改用 Typer 或 Click。
- 未经用户明确要求，不新增运行时依赖。
- 真实 SDK / 厂商接入必须收敛在 `provider/` 适配层后面。
- 工具能力默认禁用，除非调用方显式启用。
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
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw health
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw run "hello tiny claw"
uv run python -m tiny_claw --help
```

手动冒烟测试结束后，删除 `.tmp-state/`。

## 配置项

- `TINY_CLAW_LOG_LEVEL`：`DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`，默认
  `INFO`。
- `TINY_CLAW_PROVIDER`：provider 名称，默认 `echo`。
- `TINY_CLAW_MODEL`：模型名称，默认等于 provider 名称。
- `TINY_CLAW_STATE_DIR`：记忆 / 状态目录，默认 `~/.tiny-claw`。
- `TINY_CLAW_OPENAI_API_KEY`：预留给未来 OpenAI provider 适配器。

## 当前 Provider 策略

默认 `echo` provider 故意保持零运行时依赖，确保框架在没有 API key 的情况下也能运行。

`src/tiny_claw/_internal/provider/openai.py` 当前只是预留适配层。除非用户明确要求真实模型接入，
不要在这里接入 OpenAI SDK 或引入相关运行时依赖。
