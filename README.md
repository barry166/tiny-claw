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

## Run

```bash
uv sync --dev
uv run tiny-claw --help
uv run tiny-claw health
uv run tiny-claw run "hello"
uv run tiny-claw run --mode think "先分析并制定计划"
uv run tiny-claw run --mode plan-act "先规划再执行"
uv run python -m tiny_claw --help
```

`run --mode think` 会隐藏工具定义，适合“先思考/先计划/不要改代码”的场景；
`run --mode plan-act` 会先隐藏工具完成规划，再自动进入 ReAct 执行阶段。默认
`run --mode act` 会允许主循环按 ReAct 方式暴露工具定义。

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
- `TINY_CLAW_PROVIDER`: provider name; defaults to `echo`.
- `TINY_CLAW_MODEL`: provider model name; defaults to `echo`.
- `TINY_CLAW_STATE_DIR`: memory/state directory; defaults to `~/.tiny-claw`.
- `TINY_CLAW_OPENAI_API_KEY`: reserved for the future OpenAI provider adapter.
