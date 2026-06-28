# Mint

Mint is a local, Codeplain-inspired regenerative coding experiment. You write
plain `*.mint.md` specs, and `mint` renders working generated modules one
functional unit at a time, runs generated unit and conformance tests, records
checkpoints, and re-renders only the affected slice when a spec or dependency
changes. Python is the default stack; TypeScript libraries are supported through
the model/replay path with Node, `tsc --noEmit`, and Vitest.

The current v1 system is built around offline, reproducible runs:

- deterministic templates for the task-list demo modules
- record/replay model cassettes for template-free specs
- generated unit and conformance gates, plus Python coverage, traceability, and
  mutation-probe gates
- nested git checkpoints and attempt manifests under `generated/<module>/.mintgen/`
- explicit e2e tests for the public CLI workflow

## Quick Start

Use Python 3.12 or newer.

```bash
python -m pip install -e ".[dev]"
mint doctor
pytest --cov=mint_cli --cov-report=term-missing:skip-covered --cov-fail-under=80 -q
```

Start a new Mint project from an empty directory:

```bash
mint init --write
mint next
mint doctor
mint render example
mint report example
```

That gives you a complete offline smoke test. To start your own arbitrary module
after that, use the model-backed path:

```bash
ANTHROPIC_MODEL=your-anthropic-model-id
mint new notes --renderer model --model "$ANTHROPIC_MODEL" --prompt-version notes-v1
mint next notes
```

Render the built-in dependency demo:

```bash
mint render tasklist
mint render tasklist        # no-op when nothing changed
mint status tasklist
mint report tasklist
```

Render the template-free replayed model graph:

```bash
mint render calc-cli
```

Create a TypeScript library spec:

```bash
ANTHROPIC_MODEL=your-anthropic-model-id
mint new calc-ts --stack typescript-lib --renderer model \
  --model "$ANTHROPIC_MODEL" --prompt-version calc-ts-v1
$EDITOR .mint/specs/calc-ts.mint.md
mint healthcheck calc-ts
MINT_LIVE=1 mint live-smoke calc-ts
mint render calc-ts
```

Everything above runs offline. Live provider recording is manual-only:

```bash
MINT_LIVE=1 mint live-smoke calc-cli
```

That path requires `ANTHROPIC_API_KEY` and the `live` optional extra.

## Creating A New Spec

For a fresh arbitrary module in an initialized Mint project, scaffold a
model-backed spec:

```bash
ANTHROPIC_MODEL=your-anthropic-model-id
mint new notes --renderer model --model "$ANTHROPIC_MODEL" --prompt-version notes-v1
$EDITOR .mint/specs/notes.mint.md
mint lint notes
mint healthcheck notes   # points out missing replay cassettes before first recording
MINT_LIVE=1 mint live-smoke notes
```

Replace `your-anthropic-model-id` before running `mint new`; Mint rejects that
literal placeholder so it does not get recorded into a spec by accident.

The live smoke run records cassettes. After that, ordinary `mint render notes`
replays the recorded responses offline. If you want deterministic offline rendering
without a model, add a deterministic template for the module and use that template
from the spec. `mint healthcheck <module>` will tell you when a local spec is
missing a deterministic template, or when a model spec has no replay cassettes yet.

When you are unsure where you are in the workflow, ask Mint:

```bash
mint next notes
```

## Project Layout

```text
src/mint_cli/                 Mint CLI and workflow engine
.mint/specs/*.mint.md         Source specs
resources/cassettes/v1/       Offline model replay fixtures
test_scripts/                 Generated-code unit/conformance runners
tests/                        Unit, integration, replay, and e2e tests
docs/regenerative-system/     Current system documentation
generated/                    Ignored rendered output
conformance/                  Ignored generated conformance tests
```

## Core Commands

- `mint doctor` checks config, scripts/toolchains, specs, pytest/Node as needed,
  and replay fixtures.
- `mint lint <module>` checks spec quality before rendering.
- `mint next [<module>]` shows the next recommended command.
- `mint render <module>` renders a module and required dependencies.
- `mint status <module>` explains whether a render would be a no-op or a slice.
- `mint inspect <module> <FRN>` shows a unit record and attempt history.
- `mint report <module>` prints the latest run report.
- `mint clean <module> --yes` removes generated output for a module.

See [commands.md](docs/regenerative-system/commands.md) for the full command guide.

## Testing And CI

Default offline CI installs `.[dev]`, runs `mint doctor`, and runs the pytest suite
with an 80% package coverage floor. The suite includes explicit e2e tests under
`tests/e2e/` that launch the CLI in subprocesses against isolated projects.

Python generated-code scripts run pytest through `PYTHON_BIN`; the workflow defaults
that to the interpreter running `mint` so generated unit, conformance, and
test-quality checks stay on the same runtime unless explicitly overridden.
TypeScript generated modules run npm-compatible package scripts for `tsc --noEmit`
and Vitest. See [typescript.md](docs/regenerative-system/typescript.md) for the
package contract and current TS limits.

## Documentation

Start with [docs/regenerative-system/README.md](docs/regenerative-system/README.md).
That directory documents the system as built:

- architecture and render loop
- spec format
- renderer contract
- TypeScript stacks
- model record/replay
- module graph and cascade behavior
- metadata, checkpoints, reports, and budgets
- test-quality gates
- known limits and next risks

Historical planning notes live under `docs/regenerative-mvp/`, and Codeplain
research notes live under `docs/codeplain-research/`.
