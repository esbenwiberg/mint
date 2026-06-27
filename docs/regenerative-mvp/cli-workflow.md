# CLI Workflow

The MVP CLI is named `mint` in these docs. The name can change before
implementation.

## Commands

```bash
mint init
mint parse <module>
mint healthcheck <module>
mint render <module> [--from FRN] [--range FRN:FRM] [--force]
mint status <module>
mint inspect <module> <unit-id>
mint clean <module>
```

## `mint init`

Creates the local skeleton:

```text
specs/
resources/
test_scripts/
generated/
conformance/
mint.yaml
```

It also writes example scripts for the first generated target stack.

## `mint parse <module>`

Reads `specs/<module>.mint.md`, validates syntax, and prints canonical IR.

Checks:

- frontmatter exists
- module name matches filename unless overridden
- sections are known
- functional unit IDs are unique
- acceptance tests belong to a unit
- linked resources are local text files

## `mint healthcheck <module>`

Runs the pre-render gate.

Checks:

- spec parses
- config parses
- scripts exist
- scripts are executable
- generated folder metadata is valid if present
- render range is valid
- non-functional changes are detected and reported

Example output:

```text
PASS user-auth
- Spec parsed: 3 functional units
- Config parsed: mint.yaml
- Unit script: test_scripts/run_unit_tests.sh
- Conformance script: test_scripts/run_conformance_tests.sh
- Generated repo: generated/user-auth
- Last successful unit: FR2
```

If healthcheck fails, the first line is `FAIL`.

## `mint render <module>`

Default behavior:

1. Run healthcheck.
2. Parse spec into IR.
3. Determine render start:
   - no generated repo: start at first unit
   - non-functional hash changed: start at first unit
   - functional-only changes: start at earliest changed unit
   - `--from`: start at the requested unit
   - `--range`: render only the requested inclusive range
4. Prepare generated repo.
5. Render each unit.
6. Run unit and conformance gates.
7. Commit checkpoints.
8. Update metadata.

## Render Start Rules

```text
if --force:
  start = first unit
else if --from:
  start = requested unit
else if nonFunctionalSpecHash changed:
  start = first unit
else if functional unit text changed:
  start = earliest changed or moved unit
else if incomplete previous render:
  start = next incomplete unit
else:
  no-op
```

## `mint status <module>`

Shows the current generated state:

```text
Module: user-auth
Spec: specs/user-auth.mint.md
Generated: generated/user-auth
Last successful unit: FR2
Spec hash: changed
Non-functional hash: unchanged
Suggested render: mint render user-auth --from FR3
```

## `mint inspect <module> <unit-id>`

Prints:

- unit spec text
- acceptance tests
- checkpoint commits
- latest attempt logs
- test-script output summaries
- files changed by the unit

This is the main debugging entry point.

## `mint clean <module>`

Deletes generated output for a module after confirmation.

It does not delete specs, resources, config, or test scripts.

## Agent Workflows

### Add Feature

Agent flow:

1. Read the target spec.
2. Ask one writable question.
3. Write or update one spec snippet.
4. Review the snippet.
5. Repeat until covered.
6. Run `mint healthcheck`.

The agent does not edit generated code.

### Debug Specs

Agent flow:

1. Read the reported failure.
2. Run `mint inspect`.
3. Read generated code/tests as evidence.
4. Classify the root cause:
   - ambiguous spec
   - missing spec
   - conflicting spec
   - incorrect spec
   - missing implementation requirement
   - broken script
   - renderer drift
5. Edit specs/scripts only.
6. Run `mint healthcheck`.
7. Resume render with `mint render <module> --from <unit-id>`.

### Render Supervisor

Agent flow:

1. Launch or attach to `mint render`.
2. Tail `generated/<module>/.mintgen/render.log`.
3. Track current unit and attempts.
4. Stop when retry limits or drift patterns appear.
5. Route to debug/spec/script fix.
6. Resume from the failed unit.

## First Stack

The first implementation is intentionally locked down:

- `mint` implementation: Python 3.12 CLI.
- First generated target: Python CLI/library package.
- Unit tests: pytest inside `generated/<module>`.
- Conformance tests: pytest under `conformance/<module>/<unit-id>`.
- Public surface: generated library API and/or generated CLI command.

FastAPI, TypeScript, browser/UI, and integration-oriented adapters come after
the Python CLI/library loop can regenerate at least two functional units with
unit, conformance, and regression gates.
