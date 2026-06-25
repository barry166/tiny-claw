# Contributing to tiny-claw

Thanks for taking a look at `tiny-claw`. This repository is intentionally small
and architecture-focused, so the best contributions are usually narrow,
well-tested, and easy to explain.

## Good Contribution Areas

- Documentation fixes, tutorial improvements, and broken link fixes.
- Small examples that run with `TINY_CLAW_PROVIDER=echo`.
- CLI help text and error message improvements.
- Focused tests for tools, sessions, tracing, approval flow, or provider
  adapters.
- English translations of selected tutorial sections.
- Small bug fixes with a reproducible command or test.

## Before Opening a Pull Request

1. Search existing issues and pull requests.
2. Keep the change small and focused.
3. Add or update tests when behavior changes.
4. Run the local checks:

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

For CLI changes, also run:

```bash
uv run tiny-claw --help
uv run tiny-claw run --help
uv run tiny-claw serve --help
TINY_CLAW_PROVIDER=echo TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw health
TINY_CLAW_PROVIDER=echo TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw run "hello tiny claw"
```

Remove `.tmp-state/` after local smoke tests.

## Design Boundaries

- Keep public command entrypoints thin.
- Keep vendor SDK details inside `src/tiny_claw/_internal/provider/`.
- Keep tool behavior inside `src/tiny_claw/_internal/tools/`.
- Keep external platform adapters inside `src/tiny_claw/_internal/integrations/`.
- Avoid new runtime dependencies unless there is a clear reason.
- Prefer a small readable implementation over a generalized abstraction.

## Pull Request Checklist

- The PR explains the problem and the approach.
- Tests or docs were updated where appropriate.
- Local checks are listed in the PR body.
- The change does not add secrets, local state, or generated cache files.

## Live Provider Tests

Tests with `openai_live` in the filename are skipped unless the relevant API key
is available. They are useful for manual verification but should not be required
for every contributor.
