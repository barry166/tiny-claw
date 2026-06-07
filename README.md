# tiny-claw

`tiny-claw` is a layered Python CLI framework skeleton. It keeps the command
entrypoint thin and places core implementation details under
`src/tiny_claw/_internal/`, mirroring the boundary commonly expressed by Go
projects with `cmd/` and `internal/`.

## Layout

```text
src/tiny_claw/
├── __main__.py
├── cli.py
└── _internal/
    ├── app.py
    ├── context/
    ├── engine/
    ├── integrations/
    ├── memory/
    ├── provider/
    └── tools/
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the architecture design,
module boundaries, dependency flow, context engine, tool execution, and main
loop.

## Run

```bash
uv sync --dev
uv run tiny-claw --help
OPENAI_API_KEY=sk-xxx uv run tiny-claw health
OPENAI_API_KEY=sk-xxx uv run tiny-claw run "hello"
OPENAI_API_KEY=sk-xxx uv run tiny-claw run --session debug-login "continue debugging"
OPENAI_API_KEY=sk-xxx uv run tiny-claw run --mode think "先分析并制定计划"
OPENAI_API_KEY=sk-xxx uv run tiny-claw run --mode plan-act "先规划再执行"
uv run tiny-claw serve --host 0.0.0.0 --port 8000
uv run python -m tiny_claw --help
```

For an offline smoke test, use the explicit echo provider:

```bash
TINY_CLAW_PROVIDER=echo uv run tiny-claw run "hello"
```

`run --mode think` 会隐藏工具定义，适合“先思考/先计划/不要改代码”的场景；
`run --mode plan-act` 会先隐藏工具完成规划，再自动进入 ReAct 执行阶段。默认
`run --mode act` 会允许主循环按 ReAct 方式暴露工具定义。

`run --session <name>` 会为 CLI 创建独立会话记忆；不传时使用当前工作区的默认
session。飞书入口会按 `chat_id` 自动隔离上下文。

## HTTP Server and Feishu

`tiny-claw serve` starts a unified HTTP event server. The first event endpoint is
Feishu:

```text
GET  /health
POST /api/events/feishu
```

Start a local server for Feishu callback testing:

```bash
FEISHU_APP_ID=cli_xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_VERIFICATION_TOKEN=xxx \
FEISHU_ENCRYPT_KEY=xxx \
uv run tiny-claw serve --host 0.0.0.0 --port 8000
```

If the Feishu app does not configure an encrypt key or verification token, omit
the matching environment variable. Check the server locally:

```bash
curl http://127.0.0.1:8000/health
```

Feishu needs a public HTTPS callback URL. During local testing, expose the
server with a tunnel such as:

```bash
ngrok http 8000
```

Then configure the Feishu event subscription Request URL as:

```text
https://<public-host>/api/events/feishu
```

For real model responses, start the same server with provider settings, for
example:

```bash
FEISHU_APP_ID=cli_xxx \
FEISHU_APP_SECRET=xxx \
TINY_CLAW_PROVIDER=openai \
OPENAI_API_KEY=sk-xxx \
TINY_CLAW_ENABLED_TOOLS=read \
uv run tiny-claw serve --host 0.0.0.0 --port 8000
```

## Check

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## Runtime Configuration

- `TINY_CLAW_LOG_LEVEL`: one of `DEBUG`, `INFO`, `WARNING`, `ERROR`,
  `CRITICAL`; defaults to `INFO`.
- `TINY_CLAW_PROVIDER`: `openai`, `claude`, `anthropic`, or `echo`; defaults to `openai`.
- `TINY_CLAW_MODEL`: provider model name; defaults to `gpt-5.4` for OpenAI,
  `claude-sonnet-4-20250514` for Claude, otherwise the provider name.
- `TINY_CLAW_MAX_TOKENS`: maximum model output tokens; defaults to `1024`.
- `TINY_CLAW_STATE_DIR`: memory/state directory; defaults to `~/.tiny-claw`.
- `TINY_CLAW_SERVER_HOST`: HTTP server bind host; defaults to `0.0.0.0`.
- `TINY_CLAW_SERVER_PORT`: HTTP server bind port; defaults to `8000`.
- `OPENAI_API_KEY` or `OPENAI_KEY`: required for the default OpenAI provider.
- `OPENAI_BASE_URL`: optional OpenAI-compatible API base URL.
- `ANTHROPIC_API_KEY` or `CLAUDE_KEY`: required for `TINY_CLAW_PROVIDER=claude`.
- `FEISHU_APP_ID` or `LARK_APP_ID`: required for the Feishu event endpoint.
- `FEISHU_APP_SECRET` or `LARK_APP_SECRET`: required for the Feishu event endpoint.
- `FEISHU_VERIFICATION_TOKEN`: optional Feishu event verification token.
- `FEISHU_ENCRYPT_KEY`: optional Feishu event encrypt key.
- `FEISHU_EVENT_PATH`: Feishu callback path; defaults to `/api/events/feishu`.

When using the CLI, configuration is loaded in priority order: the tiny-claw
source-tree `.env`, then the current working directory `.env` for missing
values, then process environment variables for any remaining missing values.
