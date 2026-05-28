# tiny-claw Agent Context

This file is the project context for Codex and other coding agents. Read it
before changing code in this repository.

## Project Purpose

`tiny-claw` is a layered Python CLI framework skeleton inspired by Go-style
`cmd/` and `internal/` project boundaries. The user-facing command is
`tiny-claw`; the importable Python package is `tiny_claw`.

The first version is intentionally CLI-first. It should provide a clean command
entrypoint, an application assembly layer, and internal extension points for
engine, provider, context, tools, memory, and integrations.

## Architecture

- `src/tiny_claw/cli.py`: CLI parsing and command dispatch only. Keep business
  logic out of this file.
- `src/tiny_claw/__main__.py`: supports `python -m tiny_claw` by delegating to
  `tiny_claw.cli.main`.
- `src/tiny_claw/_internal/app.py`: assembles settings, provider, memory,
  tools, and engine.
- `src/tiny_claw/_internal/engine/`: main-loop orchestration.
- `src/tiny_claw/_internal/provider/`: model provider abstractions and concrete
  adapters.
- `src/tiny_claw/_internal/context/`: prompt/context construction and token
  helpers.
- `src/tiny_claw/_internal/tools/`: tool registry, middleware, and built-in
  tool skeletons.
- `src/tiny_claw/_internal/memory/`: file-backed state and memory storage.
- `src/tiny_claw/_internal/integrations/`: external integrations such as
  Feishu.

Treat `_internal` as a private implementation boundary. Public usage should go
through the `tiny-claw` command, `python -m tiny_claw`, or deliberately exposed
APIs.

## Runtime Interfaces

Supported commands:

```bash
uv run tiny-claw --help
uv run tiny-claw health
uv run tiny-claw run "hello"
uv run python -m tiny_claw --help
```

The package script entrypoint is configured in `pyproject.toml`:

```toml
[project.scripts]
tiny-claw = "tiny_claw.cli:main"
```

## Development Rules

- Use standard library `argparse` for the CLI unless the user explicitly asks
  for Typer or Click.
- Do not add runtime dependencies without an explicit user request.
- Keep provider SDK integrations behind `provider/` adapters.
- Keep tools disabled by default unless a caller explicitly enables them.
- Prefer dependency injection for engine tests so fake providers, memories, and
  tools can be used.
- Keep source code in `src/` layout and tests in `tests/`.

## Verification

Run these before claiming a change is complete:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

For CLI changes, also run:

```bash
uv run tiny-claw --help
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw health
TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw run "hello tiny claw"
uv run python -m tiny_claw --help
```

Remove `.tmp-state/` after manual smoke tests.

## Configuration

- `TINY_CLAW_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`;
  defaults to `INFO`.
- `TINY_CLAW_PROVIDER`: provider name; defaults to `echo`.
- `TINY_CLAW_MODEL`: model name; defaults to the provider name.
- `TINY_CLAW_STATE_DIR`: memory/state directory; defaults to `~/.tiny-claw`.
- `TINY_CLAW_OPENAI_API_KEY`: reserved for the future OpenAI adapter.

## Current Provider Policy

The default `echo` provider is intentionally dependency-free and makes the
framework runnable without API keys. `provider/openai.py` is currently a
placeholder adapter; do not wire OpenAI SDK behavior unless the user asks for
real model integration.
