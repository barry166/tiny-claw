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
uv run python -m tiny_claw --help
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
- `TINY_CLAW_PROVIDER`: provider name; defaults to `echo`.
- `TINY_CLAW_MODEL`: provider model name; defaults to `echo`.
- `TINY_CLAW_STATE_DIR`: memory/state directory; defaults to `~/.tiny-claw`.
- `TINY_CLAW_OPENAI_API_KEY`: reserved for the future OpenAI provider adapter.
