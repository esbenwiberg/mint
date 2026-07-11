# Mint

Mint is a local regenerative coding experiment. You write plain `*.mint.md`
specs, and `mint` renders working generated modules one functional unit at a
time, runs generated unit and conformance tests, records checkpoints, and
re-renders only the affected slice when a spec or dependency changes. Python is
the default stack; TypeScript libraries are supported through the model/replay
path with Node, `tsc --noEmit`, and Vitest.

The current v1 system is built around offline, reproducible runs:

- deterministic templates for the task-list demo modules
- record/replay model cassettes for template-free specs
- generated unit and conformance gates, plus Python coverage, traceability, and
  mutation-probe gates
- nested git checkpoints and attempt manifests under `.mint/generated/<module>/.mintgen/`
- explicit e2e tests for the public CLI workflow

## Install Mint

Mint is distributed as a Python CLI package. Use Python 3.12 or newer.

From GitHub:

```bash
python3.12 -m pip install "mint-regenerative @ git+https://github.com/esbenwiberg/mint.git"
mint --version
```

For live Anthropic API recording, install the optional `live` extra:

```bash
python3.12 -m pip install "mint-regenerative[live] @ git+https://github.com/esbenwiberg/mint.git"
```

## Use Mint In A Repo

Initialize Mint metadata in the repo you want Mint to help manage:

```bash
cd /path/to/your/repo
mint init --write
mint next
mint doctor
```

That gives you a complete offline smoke test:

```bash
mint render example
mint report example
```

`mint init --write` also adds generated-output and conformance directories to
`.gitignore` while keeping their `.gitkeep` placeholders trackable.

To let Mint handle a small area of the repo, scaffold one bounded module spec:

```bash
mint new notes --renderer claude-cli --model sonnet --prompt-version notes-v1
$EDITOR .mint/specs/notes.mint.md
mint lint notes
mint healthcheck notes
MINT_LIVE=1 mint live-smoke notes
mint render notes
```

Model providers are `model`/`anthropic` for the Anthropic API, `claude-cli` for
Claude Code, and `codex-cli` for Codex CLI.

Good first Mint-owned areas are pure helpers, parsers, formatters, rules engines,
small libraries, and CLI subcommands with a clear public API. Mint renders output
under `.mint/generated/<module>/` by default; `generatedDir` in `mint.yaml` can
point somewhere else if a repo needs it. The `.mint/specs/<module>.mint.md` file
is the source of truth.

Ordinary renders run offline from replay cassettes. Live provider recording is
manual-only:

```bash
MINT_LIVE=1 mint render notes      # live-record the current incremental plan
MINT_LIVE=1 mint live-smoke notes
```

Anthropic API providers require `ANTHROPIC_API_KEY` and the `live` optional extra.
CLI providers use the auth already configured in `claude` or `codex`.

## Demos

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
CODEX_MODEL=your-codex-model-id
mint new calc-ts --stack typescript-lib --renderer codex-cli \
  --model "$CODEX_MODEL" --prompt-version calc-ts-v1
$EDITOR .mint/specs/calc-ts.mint.md
mint healthcheck calc-ts
MINT_LIVE=1 mint live-smoke calc-ts
mint render calc-ts
```

## Creating A New Spec

For a fresh arbitrary module in an initialized Mint project, scaffold a
model-backed spec:

```bash
mint new notes --renderer claude-cli --model sonnet --prompt-version notes-v1
$EDITOR .mint/specs/notes.mint.md
mint lint notes
mint healthcheck notes   # points out missing replay cassettes before first recording
MINT_LIVE=1 mint live-smoke notes
```

For Anthropic API recording instead, use `--renderer anthropic --model MODEL_ID`
and export `ANTHROPIC_API_KEY` before `live-smoke`. Mint rejects placeholder model
ids so they do not get recorded into a spec by accident.

The live smoke run records cassettes. After that, ordinary `mint render notes`
replays the recorded responses offline. Editing a model-backed spec changes the
render prompt, so old cassettes become stale; record again with
`MINT_LIVE=1 mint render notes` for the current incremental plan, or
`MINT_LIVE=1 mint live-smoke notes` for a full forced re-record. If you want
deterministic offline rendering without a model, add a deterministic template for
the module and use that template from the spec. `mint healthcheck <module>` will
tell you when a local spec is missing a deterministic template, or when a model spec
has no replay cassettes yet.

When you are unsure where you are in the workflow, ask Mint:

```bash
mint next notes
```

## Local Development

For work on Mint itself:

```bash
python3.12 -m pip install -e ".[dev]"
mint doctor
pytest --cov=mint_cli --cov-report=term-missing:skip-covered --cov-fail-under=80 -q
```

## Project Layout

```text
src/mint_cli/                 Mint CLI and workflow engine
.mint/specs/*.mint.md         Source specs
resources/cassettes/v1/       Offline model replay fixtures
test_scripts/                 Generated-code unit/conformance runners
tests/                        Unit, integration, replay, and e2e tests
docs/regenerative-system/     Current system documentation
.mint/generated/              Ignored rendered output
conformance/                  Ignored generated conformance tests
```

## Core Commands

- `mint doctor` checks config, scripts/toolchains, specs, pytest/Node as needed,
  and replay fixtures.
- `mint lint <module>` checks spec quality before rendering.
- `mint next [<module>]` shows the next recommended command.
- `mint render <module>` renders a module and required dependencies.
- `mint status <module>` explains whether a render would be a no-op or a slice.
- `mint drift <module>` shows hand edits in generated output since the last
  checkpoint.
- `mint inspect <module> <FRN>` shows a unit record and attempt history.
- `mint report <module>` prints the latest run report.
- `mint clean <module> --yes` removes generated output for a module.
- `mint prune [--yes]` deletes replay cassettes no rendered module references.

See `docs/regenerative-system/commands.md` for the full command guide.

## Testing And CI

Default offline CI installs `.[dev]`, runs `mint doctor`, and runs the pytest suite
with an 80% package coverage floor. The suite includes explicit e2e tests under
`tests/e2e/` that launch the CLI in subprocesses against isolated projects.

Python generated-code scripts run pytest through `PYTHON_BIN`; the workflow defaults
that to the interpreter running `mint` so generated unit, conformance, and
test-quality checks stay on the same runtime unless explicitly overridden.
TypeScript generated modules run npm-compatible package scripts for `tsc --noEmit`
and Vitest. See `docs/regenerative-system/typescript.md` for the package contract
and current TS limits.

## Documentation

For writing good specs — testable acceptance, blast-radius-aware section
placement, the UI style-lock pattern, and the record/replay/prune workflow —
read `docs/spec-authoring.md`.

Start with `docs/regenerative-system/README.md`. That directory documents the
system as built:

- architecture and render loop
- spec format
- renderer contract
- TypeScript stacks
- model record/replay
- module graph and cascade behavior
- metadata, checkpoints, reports, and budgets
- test-quality gates
- known limits and next risks

Historical planning notes live under `docs/regenerative-mvp/`.
