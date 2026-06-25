# Echo Provider Smoke Test

This example proves the CLI, settings, session memory, usage tracking, and trace
writer can run without a model API key.

```bash
uv sync --dev
TINY_CLAW_PROVIDER=echo TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw health
TINY_CLAW_PROVIDER=echo TINY_CLAW_STATE_DIR=.tmp-state uv run tiny-claw run --mode think "hello tiny claw"
```

Expected health output:

```text
status=ok
provider=echo
tools=read
state_dir=.tmp-state
```

The final CLI response should include:

```text
hello tiny claw
```

Inspect generated local state:

```bash
find .tmp-state -maxdepth 4 -type f | sort
```

Remove local state when you are done:

```bash
rm -rf .tmp-state
```
