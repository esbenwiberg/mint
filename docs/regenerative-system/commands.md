# Commands

Run as `mint <command> …` (or `python -m mint_cli <command> …`). Module-oriented
commands take a module name; `render` takes optional range flags. Errors are
`MintError`s printed as `ERROR: …` on stderr with a non-zero exit, and include file
paths and fix hints.

## `mint parse <module>`

Parse the spec and print the canonical JSON IR (sorted, stable). Useful for diffing
specs and for debugging hashes.

```bash
mint parse tasklist
```

## `mint new <module> [--requires ...]`

Scaffold a parseable starter spec under `specs/<module>.mint.md`.

```bash
mint new calc-cli --requires evaluator
# Wrote specs/calc-cli.mint.md
```

The starter is template-free. If dependencies are supplied, they are added to both
`imports` and `requires` so the module graph and prompt context start wired.

## `mint lint <module>`

Check spec quality beyond parsing. The lint gate fails vague acceptance criteria and
units with no testable assertion; it warns when no edge/error coverage hint is
present.

```bash
mint lint calc-cli
```

## `mint doctor`

Check the project root: `mint.yaml`, executable scripts, pytest availability, spec
parsing, missing `imports`/`requires`, and replay cassettes when the model renderer is
configured.

```bash
mint doctor
```

## `mint healthcheck <module>`

Validate everything needed before a render: spec parses, scripts exist and are
executable, `imports`/`requires` resolve, linked resources exist, existing metadata
is valid. Exit 0 = `PASS`, exit 1 = `FAIL` with reasons.

```bash
mint healthcheck tasklist
# FAIL tasklist
# - Unit script is not executable: test_scripts/run_unit_tests.sh (fix: chmod +x …)
```

## `mint status <module>`

Show the generated state and what a render would do: last successful unit, which
hashes changed, and the suggested command.

```bash
mint status tasklist
# Suggested render (functional unit changed: FR2): mint render tasklist --from FR2
```

## `mint render <module> [--from FRN] [--range FRN:FRM] [--force]`

Render the module and everything it requires, required-first. With no flags it does
the minimal work the plan implies (often a no-op).

```bash
mint render tasklist                 # render/refresh the whole dependency graph
mint render taskstore --from FR2     # re-render FR2 onward
mint render taskstore --range FR1:FR1 # re-render just FR1
mint render taskstore --force        # full re-render regardless of hashes
```

`--from` and `--range` are mutually exclusive and apply only to the named module;
required modules always use their incremental plan.

Successful renders write:

```text
generated/<module>/.mintgen/reports/latest.json
generated/<module>/.mintgen/reports/latest.txt
```

The JSON report contains per-unit attempts, classifications, wall-clock seconds,
estimated prompt/response tokens, cost estimate, cassette ids, and test-quality
verdicts.

If `limits.maxRenderAttempts` or `limits.maxRenderTokensEstimate` is exceeded, the
render aborts and writes `generated/<module>/.mintgen/reports/budget-abort.json`.

## `mint live-smoke <module>`

Force a live model render, recording fresh replay cassettes. This command refuses
unless `MINT_LIVE=1` is set, and it also requires `ANTHROPIC_API_KEY`.

```bash
MINT_LIVE=1 mint live-smoke calc-cli
```

Default CI never runs this command. Use it manually, or through the manual
`live-record` GitHub Actions workflow, when cassettes need to be refreshed from the
real provider.

## `mint report <module>`

Print the latest run report and refresh `latest.json` from current metadata and
attempt manifests.

```bash
mint report tasklist
# RUN REPORT tasklist
# - Attempts: 4
# - Tokens estimate: ...
```

## `mint inspect <module> <unit-id>`

Show a unit's spec/acceptance, its metadata record, and its attempt history.

```bash
mint inspect tasklist FR1
# Unit: FR1
# Record:
# - status: passed
# Attempts:
# - unit-1.json: unit attempt=1 renderer=deterministic exit=0 [passed] …
```

## `mint clean <module> --yes`

Remove the module's generated output and conformance tests. Refuses without `--yes`.

```bash
mint clean tasklist --yes
```

## `mint init`

Print the expected project skeleton (config, specs, scripts, output dirs).

## End-to-end demo

```bash
mint render tasklist          # renders taskstore then tasklist; all gates pass
mint render tasklist          # NOOP taskstore / NOOP tasklist

# Editing one later unit re-renders only that slice:
$EDITOR specs/tasklist.mint.md   # change FR2
mint render tasklist             # Range: FR2:FR2

# Editing a required module cascades to its dependents:
$EDITOR specs/taskstore.mint.md  # change FR2
mint render tasklist             # taskstore FR2, then tasklist (required code changed)
```

## Replayed model calc graph

The template-free expression-language demo is rendered through the model path with
local replay cassettes:

```bash
mint clean calc-cli --yes
mint clean evaluator --yes
mint clean parser --yes
mint clean lexer --yes
mint render calc-cli
```

Expected order:

```text
RENDER lexer
RENDER parser
RENDER evaluator
RENDER calc-cli
```

The specs live at `specs/lexer.mint.md`, `specs/parser.mint.md`,
`specs/evaluator.mint.md`, and `specs/calc-cli.mint.md`. They have no `template`
key; each opts into `rendererProvider: model` and replays from
`resources/cassettes/v1/`.

## Self-hosting proof

`specs/mint-hashing.mint.md` renders a model-backed implementation of the hashing
helpers into `generated/mint-hashing/`. The test suite compares that generated
package against the handwritten `mint_cli.hashing` behavior.

```bash
mint render mint-hashing
```
