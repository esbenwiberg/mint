# Regenerative system — documentation

`mint` is a local, Codeplain-inspired **regenerative coding system**. You write
specs (`*.mint.md`); `mint` renders them into working, tested code, one functional
unit at a time, and re-renders only the slices affected when a spec or a dependency
changes.

This directory documents the system as built. It supersedes `docs/regenerative-mvp/`
(kept for history).

For the repo landing page and quickstart, see [`../../README.md`](../../README.md).

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Components, the render loop, data flow |
| [renderer-contract.md](renderer-contract.md) | The file-patch contract and adapter interface |
| [record-replay.md](record-replay.md) | Model cassette format, replay defaults, live re-record flow |
| [test-quality.md](test-quality.md) | Coverage, acceptance-traceability, and mutation anti-weak-test gates |
| [cost-and-budgets.md](cost-and-budgets.md) | Attempt and token budgets, abort reports, cost estimate limits |
| [release-and-ci.md](release-and-ci.md) | Package metadata, offline CI, manual live-record workflow |
| [spec-format.md](spec-format.md) | `*.mint.md` structure and validation rules |
| [typescript.md](typescript.md) | Supported TypeScript stacks, package scripts, and current limits |
| [module-graph.md](module-graph.md) | `imports`, `requires`, ordering, cascades |
| [metadata-and-checkpoints.md](metadata-and-checkpoints.md) | `.mintgen/module.json`, attempts, git checkpoints |
| [commands.md](commands.md) | Every CLI command with examples |
| [known-limits.md](known-limits.md) | What it does not do yet, and the next risks |

## 60-second tour

```bash
# In a fresh directory, create the Mint project skeleton.
mint init --write
mint doctor
mint render example

# Render a module and everything it requires, in dependency order.
mint render tasklist

# Nothing changed → no-op.
mint render tasklist

# Edit a later unit in .mint/specs/tasklist.mint.md, then:
mint render tasklist        # re-renders only from the changed unit

# Edit .mint/specs/taskstore.mint.md (a required module), then:
mint render tasklist        # re-renders taskstore, then tasklist (its code dep moved)

# Replayed model path, no templates:
mint render calc-cli        # renders lexer -> parser -> evaluator -> calc-cli
mint render mint-hashing    # renders a mint hashing component via replay

# New arbitrary specs opt into the model renderer:
mint new notes --renderer claude-cli --model sonnet --prompt-version notes-v1
MINT_LIVE=1 mint live-smoke notes

# New TypeScript library specs use the same model/replay path:
CODEX_MODEL=your-codex-model-id
mint new calc-ts --stack typescript-lib --renderer codex-cli \
  --model "$CODEX_MODEL" --prompt-version calc-ts-v1
```

Ordinary renders run offline. The default renderer is deterministic; the
model-backed renderer replays local cassettes by default and records live responses
only behind `MINT_LIVE=1`. The replay fixtures include both the calculator graph
and a self-hosted `mint-hashing` component checked for parity with
`mint_cli.hashing`.

## Current Gates

Offline CI installs `.[dev]`, runs `mint doctor`, and runs pytest with an 80%
coverage floor. The default suite includes `tests/e2e/`, which drives `mint` through
subprocesses against isolated project directories.

Python generated-code unit, conformance, and test-quality checks run through
`PYTHON_BIN`. TypeScript modules use the stack adapter to run Node/npm package
scripts: `tsc --noEmit`, Vitest unit tests, and Vitest conformance tests. TypeScript
test-quality runs too — Vitest v8 coverage, acceptance traceability, and a
TypeScript-compiler-API mutation probe — and hard-fails when the required dev tooling
(`@vitest/coverage-v8`, `typescript`) is not installed.
