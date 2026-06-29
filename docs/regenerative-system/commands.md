# Commands

Run as `mint <command> …` (or `python -m mint_cli <command> …`). Module-oriented
commands take a module name; `render` takes optional range flags. Errors are
`MintError`s printed as `ERROR: …` on stderr with a non-zero exit, and include file
paths and fix hints.

## `mint init [--write]`

Print the expected project skeleton, or create the missing files in the current
directory.

```bash
mint init
mint init --write
```

`--write` creates the standard Mint project shape without overwriting existing
files:

```text
mint.yaml
.mint/specs/example.mint.md
resources/
.mint/generated/
conformance/
test_scripts/
.gitignore
```

It also writes the default generated-code test scripts and makes newly created
scripts executable. The generated and conformance directories are added to
`.gitignore` with `.gitkeep` exceptions, including custom paths from an existing
`mint.yaml`. Existing files are kept and reported. The seeded `example` module uses
a built-in deterministic template, so the first smoke path is fully offline:

```bash
mint doctor
mint next
mint render example
mint report example
```

You can also rerun `mint init --write` later to restore missing default directories,
the seeded example spec, or default test scripts without overwriting existing files.

## `mint parse <module>`

Parse the spec and print the canonical JSON IR (sorted, stable). Useful for diffing
specs and for debugging hashes.

```bash
mint parse tasklist
```

## `mint new <module> [--stack STACK] [--requires ...] [--renderer PROVIDER]`

Scaffold a parseable starter spec under the configured `specsDir`
(`.mint/specs` by default).

```bash
mint new calc-cli --requires evaluator
# Wrote .mint/specs/calc-cli.mint.md
```

The default starter uses `stack: python-lib`. Pass `--stack typescript-lib` or
`--stack typescript-node` for TypeScript. If dependencies are supplied, they are
added to both `imports` and `requires` so the module graph and prompt context start
wired.

Fresh arbitrary specs should opt into a model renderer. Supported live providers
are `model`/`anthropic` for the Anthropic API, `claude-cli` for Claude Code, and
`codex-cli` for Codex CLI:

```bash
mint new notes --renderer claude-cli --model sonnet --prompt-version notes-v1
mint lint notes
MINT_LIVE=1 mint live-smoke notes
```

Use `--renderer anthropic --model MODEL_ID` for Anthropic API recording. Mint
rejects placeholder model ids and requires `--prompt-version` for model-backed
specs.

After the first live render records cassettes, normal offline `mint render notes`
replays those fixtures.

TypeScript modules use the same model/replay flow:

```bash
CODEX_MODEL=your-codex-model-id
mint new calc-ts --stack typescript-lib --renderer codex-cli \
  --model "$CODEX_MODEL" --prompt-version calc-ts-v1
mint lint calc-ts
mint healthcheck calc-ts
MINT_LIVE=1 mint live-smoke calc-ts
mint render calc-ts
```

Generated TypeScript packages must provide npm-compatible scripts for
`tsc --noEmit`, `vitest run tests`, and `vitest run`; see
[typescript.md](typescript.md).

## `mint lint <module>`

Check spec quality beyond parsing. The lint gate fails vague acceptance criteria,
units with no testable assertion, and relational numeric thresholds that do not
have an exact-boundary acceptance example. It accepts assertion phrasing such as
`returns`, `==`, and `fn(x) → true`. It warns when no edge/error coverage hint is
present.

```bash
mint lint calc-cli
```

## `mint next [<module>]`

Show the next recommended action for the project or for one module. This is the
guided entry point when you are not sure whether to initialize, scaffold, lint,
healthcheck, record cassettes, render, or inspect reports.

```bash
mint next
# NEXT example
# - State: ready to render (no generated metadata).
# - Next command: mint render example

mint next notes
# NEXT notes
# - State: pre-render checks need attention.
# - Next command: MINT_LIVE=1 mint live-smoke notes
```

## `mint doctor`

Check the project root: `mint.yaml`, executable scripts, stack toolchains
(pytest for Python, Node/npm for TypeScript), spec parsing, missing
`imports`/`requires`, local renderer templates, and replay cassettes when the model
renderer is configured.

```bash
mint doctor
```

## `mint healthcheck <module>`

Validate everything needed before a render: spec parses, the selected stack adapter
can prepare its toolchain, `imports`/`requires` resolve, renderer/template selection
can run, linked resources exist, existing metadata is valid. Exit 0 = `PASS`, exit
1 = `FAIL` with reasons.

```bash
mint healthcheck tasklist
# FAIL tasklist
# - Unit script is not executable: test_scripts/run_unit_tests.sh (fix: chmod +x …)
```

For a fresh local starter spec, healthcheck fails until you either add/select a
deterministic template or switch the spec to a model provider such as
`rendererProvider: claude-cli`. For a fresh model spec, healthcheck fails in offline
mode until replay cassettes exist for that module/model/prompt version. Use
`MINT_LIVE=1 mint render <module>` to live-record the current render plan, or
`MINT_LIVE=1 mint live-smoke <module>` to force-record the full module.

For TypeScript specs, healthcheck checks Node and npm. Package-script validation
happens at render/test time so a new model patch can create or repair
`package.json`.

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

If the generated directory already exists but has no `.mintgen/module.json` (for
example, copied output from another checkout), normal render refuses to touch it.
Run `mint render <module> --force` only when you intentionally want Mint to replace
that directory.

Successful renders write:

```text
.mint/generated/<module>/.mintgen/reports/latest.json
.mint/generated/<module>/.mintgen/reports/latest.txt
```

The JSON report contains per-unit attempts, classifications, wall-clock seconds,
estimated prompt/response tokens, cost estimate, cassette ids, and test-quality
verdicts.

If `limits.maxRenderAttempts` or `limits.maxRenderTokensEstimate` is exceeded, the
render aborts and writes `.mint/generated/<module>/.mintgen/reports/budget-abort.json`.

## `mint live-smoke <module>`

Force a live model render, recording fresh replay cassettes. This command refuses
unless `MINT_LIVE=1` is set. Anthropic API providers also require
`ANTHROPIC_API_KEY`; CLI providers use the auth already configured in their CLI.

```bash
MINT_LIVE=1 mint live-smoke calc-cli
```

Default CI never runs this command. Use it manually, or through the manual
`live-record` GitHub Actions workflow, when cassettes need to be refreshed from the
real provider.

For a spec edit on a model-backed module, ordinary offline `mint render` may fail
because the prompt text changed and the old cassette is stale. Use
`MINT_LIVE=1 mint render <module>` when you want to live-record only the incremental
plan, or `MINT_LIVE=1 mint live-smoke <module>` when the demo needs a full re-record.

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

## End-to-end demo

```bash
mint render tasklist          # renders taskstore then tasklist; all gates pass
mint render tasklist          # NOOP taskstore / NOOP tasklist

# Editing one later unit re-renders only that slice:
$EDITOR .mint/specs/tasklist.mint.md   # change FR2
mint render tasklist             # Range: FR2:FR2

# Editing a required module cascades to its dependents:
$EDITOR .mint/specs/taskstore.mint.md  # change FR2
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

The specs live at `.mint/specs/lexer.mint.md`, `.mint/specs/parser.mint.md`,
`.mint/specs/evaluator.mint.md`, and `.mint/specs/calc-cli.mint.md`. They have no
`template` key; each opts into `rendererProvider: model` and replays from
`resources/cassettes/v1/`.

## Self-hosting proof

`.mint/specs/mint-hashing.mint.md` renders a model-backed implementation of the hashing
helpers into `.mint/generated/mint-hashing/`. The test suite compares that generated
package against the handwritten `mint_cli.hashing` behavior.

```bash
mint render mint-hashing
```
