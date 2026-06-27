# Regenerative system — documentation

`mint` is a local, Codeplain-inspired **regenerative coding system**. You write
specs (`*.mint.md`); `mint` renders them into working, tested code, one functional
unit at a time, and re-renders only the slices affected when a spec or a dependency
changes.

This directory documents the system as built. It supersedes `docs/regenerative-mvp/`
(kept for history).

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Components, the render loop, data flow |
| [renderer-contract.md](renderer-contract.md) | The file-patch contract and adapter interface |
| [record-replay.md](record-replay.md) | Model cassette format, replay defaults, live re-record flow |
| [test-quality.md](test-quality.md) | Coverage, acceptance-traceability, and mutation anti-weak-test gates |
| [cost-and-budgets.md](cost-and-budgets.md) | Attempt and token budgets, abort reports, cost estimate limits |
| [release-and-ci.md](release-and-ci.md) | Package metadata, offline CI, manual live-record workflow |
| [spec-format.md](spec-format.md) | `*.mint.md` structure and validation rules |
| [module-graph.md](module-graph.md) | `imports`, `requires`, ordering, cascades |
| [metadata-and-checkpoints.md](metadata-and-checkpoints.md) | `.mintgen/module.json`, attempts, git checkpoints |
| [commands.md](commands.md) | Every CLI command with examples |
| [known-limits.md](known-limits.md) | What it does not do yet, and the next risks |

## 60-second tour

```bash
# Render a module and everything it requires, in dependency order.
mint render tasklist

# Nothing changed → no-op.
mint render tasklist

# Edit a later unit in specs/tasklist.mint.md, then:
mint render tasklist        # re-renders only from the changed unit

# Edit specs/taskstore.mint.md (a required module), then:
mint render tasklist        # re-renders taskstore, then tasklist (its code dep moved)

# Replayed model path, no templates:
mint render calc-cli        # renders lexer -> parser -> evaluator -> calc-cli
mint render mint-hashing    # renders a mint hashing component via replay
```

Everything runs offline. The default renderer is deterministic; the model-backed
renderer replays local cassettes by default and records live responses only behind
`MINT_LIVE=1`. The replay fixtures include both the calculator graph and a
self-hosted `mint-hashing` component checked for parity with `mint_cli.hashing`.
