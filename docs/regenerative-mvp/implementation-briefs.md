# Implementation Briefs

These briefs are ordered so each phase proves one new part of the regenerative
loop. Each phase should leave the repo in a coherent state with docs and tests.

## Phase 0: Skeleton And Conventions

Objective: make the project shape explicit before code.

Deliverables:

- Python 3.12 project skeleton for the `mint` CLI
- `mint.yaml` example
- `.mint/specs/example.mint.md`
- `test_scripts/` example scripts for Python CLI/library generation
- `.gitignore` entries for generated artifacts if needed
- short contributor note: generated code is read-only

Acceptance:

- `mint.yaml` and the example spec are valid examples of the documented format.
- `mint --help` runs locally.
- The first target stack is documented as Python CLI/library + pytest.

## Phase 1: Parser And IR

Objective: parse one `*.mint.md` spec into canonical JSON.

Deliverables:

- parser module
- IR types/schema
- `mint parse <module>`
- parser tests for:
  - frontmatter
  - definitions
  - implementation reqs
  - test reqs
  - functional units
  - acceptance tests
  - duplicate unit IDs
  - missing sections

Acceptance:

- `mint parse example` prints deterministic JSON.
- Reformatting whitespace does not change semantic hashes.

## Phase 2: Healthcheck

Objective: catch cheap failures before render.

Deliverables:

- config parser
- script path validation
- executable-bit validation on Unix
- local resource validation
- generated metadata validation
- `mint healthcheck <module>`

Acceptance:

- PASS/FAIL appears on the first line.
- All failures include a file path and concrete fix hint.
- No model/API calls are made.

## Phase 3: Generated Repo And Metadata

Objective: create checkpoint-owned generated output.

Deliverables:

- `generated/<module>` creation
- nested Git initialization
- `.mintgen/module.json`
- commit helpers
- hash calculation
- `mint status <module>`

Acceptance:

- Running status after initialization shows no completed units.
- Metadata records spec hash and non-functional hash.
- Generated repo commits can be found by unit ID.

## Phase 4: Unit Render Loop

Objective: render one unit and pass unit tests.

Deliverables:

- model-call adapter interface
- prompt builder
- structured patch applier
- unit-test runner
- retry once with failure output
- checkpoint after completion
- attempt logs

Acceptance:

- `mint render example --range FR1:FR1` renders one unit.
- Unit test script passes.
- Metadata marks FR1 as passed.
- Re-running without spec changes is a no-op.

## Phase 5: `--from` And Change Detection

Objective: regenerate from an affected unit.

Deliverables:

- compare current spec to metadata
- detect non-functional changes
- detect functional unit edits/adds/removals/moves
- reset generated repo to checkpoint before start unit
- `--from` support
- `--range` support

Acceptance:

- Editing FR2 suggests rendering from FR2.
- Editing implementation reqs suggests rendering from FR1.
- `mint render example --from FR2` preserves FR1 checkpoint and regenerates FR2+.

## Phase 6: Conformance Gate

Objective: add black-box behavior verification.

Deliverables:

- conformance test generator prompt
- conformance test output folder
- conformance script runner
- acceptance tests included in conformance prompt
- retry once with failure output
- regression over prior completed units

Acceptance:

- FR1 generates conformance tests under `conformance/<module>/FR1/`.
- Conformance script passes.
- A changed implementation that breaks FR1 is caught during FR2 regression.

## Phase 7: Supervisor

Objective: detect non-converging loops and route fixes.

Deliverables:

- append-only render log
- attempt state tracking
- retry classification
- `mint inspect <module> <unit-id>`
- stop conditions for:
  - same assertion failing
  - script infrastructure failure
  - no tests discovered
  - conformance assertion weakening

Acceptance:

- A repeated failing assertion stops the render and reports the likely spec gap.
- `mint inspect` shows prompts, patches, commits, and script output paths.

## Phase 8: Agent Authoring Skills

Objective: make spec maintenance repeatable.

Deliverables:

- `add-feature` skill/prompt
- `debug-specs` skill/prompt
- `healthcheck` skill/prompt
- `render-supervisor` skill/prompt
- examples of good writable questions

Acceptance:

- Adding a feature modifies only specs/resources/scripts.
- Debugging a generated-code bug produces a spec/script change, not a generated
  code edit.

## Explicit Deferrals

Do not implement these before Phase 6 is stable:

- import modules
- requires chains
- additional stack adapters, including FastAPI, TypeScript, and browser/UI
- full Plain parser
- schema/golden fixture registry
- browser-based UI verification
- generated-code publishing
- autonomous multi-agent repair
