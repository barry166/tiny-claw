# Roadmap

`tiny-claw` is a readable agent harness reference implementation. The roadmap
prioritizes clarity, runnable examples, and architecture lessons over breadth.

## Near Term

- Add README screenshots or terminal recordings for the first-run experience.
- Add more small examples that run with the zero-key `echo` provider.
- Translate the tutorial index and opening chapter into English.
- Add issue templates for bug reports, examples, and tutorial improvements.
- Keep CI green for lint, typecheck, and tests.

## Agent Harness Depth

- Document the trace JSON schema with one real example.
- Add a minimal custom tool tutorial.
- Add a minimal custom provider tutorial.
- Add a focused approval-flow example that does not require Feishu credentials.
- Expand session memory examples with before/after files.

## Integration Direction

- Keep Feishu/Lark support as an adapter, not a second runtime.
- Keep provider adapters thin and model-independent.
- Prefer small local-first observability over heavy telemetry dependencies.

## Non-Goals

- Becoming a hosted agent platform.
- Competing with full workflow systems or graph runtimes.
- Adding many providers before the existing boundaries are easy to learn.
- Turning `_internal` modules into a broad public SDK before the reference
  implementation stabilizes.
