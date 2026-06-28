# Architecture

## System Shape

The MVP has six components:

1. **Spec files** under `.mint/specs/`.
2. **Parser** that converts Markdown specs into a structured IR.
3. **Planner** that decides which module and functional units to render.
4. **Renderer** adapter that writes code/test changes.
5. **Verifier** that runs unit and conformance scripts.
6. **Supervisor** that watches retries and routes fixes back to specs/scripts.

`mint` itself should be implemented as a Python 3.12 CLI. The first generated
target stack is also Python: a small CLI/library package verified with pytest.
The first renderer is deterministic and local so the regeneration loop is
testable without network or API keys. Model-backed rendering, FastAPI,
TypeScript, browser/UI, and other adapters come after the first loop is stable.

```text
.mint/specs/*.mint.md
    |
    v
parser -> spec IR -> planner -> renderer -> generated/<module>/
                              |              |
                              |              v
                              |         nested Git checkpoints
                              |
                              v
                         verifier scripts
                              |
                              v
                         supervisor log
```

## MVP Decisions

### Spec Surface

Use a simplified Markdown-plus-YAML format: `*.mint.md`.

Reasons:

- It matches the research pack's recommendation to start with
  Markdown+frontmatter rather than full Plain compatibility.
- It is easy for agents and humans to edit.
- It still preserves the important sections: definitions, implementation, test,
  functional units, and acceptance tests.
- The parser can emit a canonical JSON IR, leaving a future path to Plain or
  stricter YAML.

Plain compatibility is a later export/import target, not the first authoring
surface.

### Render Unit

The atomic unit is a functional unit:

```md
- id: FR1
  title: Signup creates a user
```

Units render chronologically. The renderer sees:

- definitions
- implementation requirements
- test requirements
- all previous functional units
- the current functional unit
- linked local resources
- current generated files
- relevant memory/checkpoint metadata

Future functional units are not included while rendering the current unit.

### Generated Output

Generated output lives under:

```text
generated/<module>/
```

Each module folder is initialized as its own Git repo. The main repo can ignore
or version selected generated artifacts later, but render checkpoints should not
pollute the main repo history.

### Metadata

Metadata lives at:

```text
generated/<module>/.mintgen/module.json
```

It tracks hashes, successful units, prompt/model versions, and checkpoint commit
IDs. See [Data Model](./data-model.md).

### Test Gates

The MVP uses two gates:

1. **Unit tests** inside or alongside generated code.
2. **Conformance tests** as black-box behavior tests.

Unit tests are a fast inner loop. Conformance tests are the behavior gate.
Acceptance tests in the spec become extra conformance scenarios for important
workflows.

### Render Loop

The MVP loop for each unit:

1. Roll back generated repo to the checkpoint before the unit.
2. Gather prompt context.
3. Ask the renderer for implementation and unit-test changes.
4. Apply a structured patch.
5. Run the unit-test script.
6. Retry once with failure output.
7. Ask the renderer for conformance tests for the unit.
8. Run the conformance-test script.
9. Retry once by changing either implementation or conformance tests.
10. Run prior conformance tests as regression.
11. Commit a checkpoint and update metadata.

This is intentionally smaller than Codeplain's full state machine. It keeps the
load-bearing gates and checkpoints while skipping refactor phases, memory
summaries, and ambiguity analysis until the loop works.

## Directory Layout

```text
.mint/
  specs/
    example.mint.md

resources/
  example-openapi.yaml
  fixtures/

test_scripts/
  run_unit_tests.sh
  run_conformance_tests.sh
  prepare_environment.sh

generated/
  example/
    .git/
    .mintgen/
      module.json
      render.log
      attempts/
    src/
    tests/

conformance/
  example/
    FR1/
    FR2/

mint.yaml
```

## Config

Root config:

```yaml
version: 1
defaultStack: python-cli
generatedDir: generated
conformanceDir: conformance
scripts:
  unit: test_scripts/run_unit_tests.sh
  conformance: test_scripts/run_conformance_tests.sh
  prepare: test_scripts/prepare_environment.sh
renderer:
  provider: local
  model: deterministic-python-cli-v0
  promptVersion: v0
limits:
  unitRetries: 1
  conformanceRetries: 1
  maxFunctionalUnitsPerRender: 20
```

## Healthcheck

`mint healthcheck` is the cheap gate before rendering. It validates:

- spec file exists and parses
- unit IDs are unique and ordered
- required sections exist
- local resource links resolve
- config file parses
- script paths exist
- scripts are executable on Unix
- generated output is either absent or has valid metadata
- render range points at existing units

Later healthcheck can dry-run prompt assembly and graph rendering.

## Supervisor

The supervisor reads append-only logs and tracks:

- current unit
- last complete unit
- unit-test attempt count
- conformance attempt count
- latest failing test
- whether failures are shrinking

It stops and reports when:

- the same assertion fails after the retry limit
- conformance tests are weakened
- generated code hard-codes around a test
- scripts fail before running tests
- scripts discover no tests
- a linked resource is missing

The fix goes into specs/scripts, then healthcheck runs again.

## Deferred Capabilities

Defer until after the first loop works:

- full Plain parser
- import modules
- requires chain
- additional generated target stacks
- model-backed renderer adapter
- rich prompt memory
- automatic spec ambiguity analysis
- conformance-test diff classifier
- golden fixture registry
- browser/UI verification
- publishing generated code elsewhere
