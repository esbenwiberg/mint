# Build Our Own

This is a practical plan for a local Codeplain-inspired system. It is not a
clone of the hosted renderer. It copies the useful architecture:

- specs as source
- generated code as output
- small render units
- tests as gates
- metadata and Git checkpoints
- supervised retry loops

## Goal

Build a local regenerative coding workflow where:

- specs are maintained in the repo
- generated code can be deleted and recreated
- partial regeneration starts at a known unit
- conformance tests catch behavior drift
- agents edit specs, not generated code

## MVP Spec Format

We do not need full Plain compatibility at first. A Markdown-plus-frontmatter
format is enough:

```md
---
name: user-auth
imports:
  - base
requires: []
---

## definitions

- User: has an email and password hash.

## implementation

- Use Python 3.12 and FastAPI.
- Unit tests use pytest.

## test

- Conformance tests use pytest and HTTPX.

## functional

- FR1: POST /signup creates a User.
  - Duplicate email returns 409.

  ### acceptance
  - Creating the same email twice returns 409 and leaves one row.
```

Compatibility with Plain can come later. The MVP needs deterministic parsing and
small render units more than perfect syntax.

## MVP Components

### 1. Parser

Input:

- spec files
- frontmatter
- sections
- functional specs
- acceptance tests
- linked local resources

Output:

- module graph
- imports
- requires
- definitions
- implementation reqs
- test reqs
- list of render units

### 2. Module Graph

Build two graphs:

- import graph for shared context
- requires graph for build order

Rules:

- imports cannot contain functional units
- requires must have compatible stacks
- top modules are modules not required by another module

### 3. Metadata Store

Store metadata next to generated output:

```text
generated/<module>/.mintgen/module.json
```

Track:

- spec hash
- non-functional spec hash
- imported context hash
- required module code hash
- rendered functional unit text
- last successful unit ID
- model and prompt versions

### 4. Git Checkpoints

Initialize generated output as a Git repo or use a repo-local checkpoint store.
Git is attractive because diff, rollback, and commit search are solved.

Commit after:

- initial generation
- each functional unit implementation
- test fixes
- refactor
- conformance pass
- unit fully complete

Commit messages should include:

- module name
- unit ID
- render ID
- phase

### 5. Renderer

For each unit:

1. Gather previous generated files.
2. Gather definitions and implementation reqs.
3. Gather previous functional specs.
4. Gather linked resources.
5. Ask an LLM for implementation changes and unit tests.
6. Apply a structured patch.
7. Run unit tests.
8. Retry with failure output.
9. Generate conformance tests.
10. Run conformance tests.
11. Retry implementation or tests based on classifier.
12. Run regression tests for earlier units.

For model calls, prefer structured JSON patch outputs or unified diffs. Avoid
free-form "here are files" if the tool can enforce structure.

### 6. Test Scripts

Follow Codeplain's config style:

```yaml
unit-tests-script: test_scripts/run_unit_tests.sh
conformance-tests-script: test_scripts/run_conformance_tests.sh
prepare-environment-script: test_scripts/prepare_environment.sh
```

The renderer should treat scripts as black-box contracts:

- exit code 0 means pass
- configured non-zero codes can mean environment failure
- stdout/stderr becomes model feedback

### 7. Supervisor

A watcher should read only appended log bytes and track:

- current unit
- last complete unit
- attempt count
- latest failing test
- whether failure count is shrinking

It should stop and ask for a spec/script fix when:

- the same assertion fails repeatedly
- conformance tests are weakened
- generated code hard-codes around a test
- a script cannot discover tests
- a toolchain dependency is missing
- retry count exceeds the threshold

### 8. Spec Authoring Skills

Create local skills or prompts equivalent to:

- `init-spec-project`
- `add-feature`
- `add-functional-unit`
- `add-concept`
- `add-acceptance-test`
- `healthcheck`
- `render-supervisor`
- `debug-specs`

The authoring discipline matters as much as the renderer.

## Suggested Build Phases

### Phase 1: Manual spec plus one-module renderer

- Define a simple spec syntax.
- Parse one module.
- Generate implementation and unit tests for one unit.
- Run tests.
- Retry once.

### Phase 2: FRID checkpoints

- Assign stable unit IDs.
- Add metadata.
- Commit generated output after each unit.
- Support `--render-from`.

### Phase 3: Conformance tests

- Generate tests outside implementation code.
- Run conformance script.
- Add acceptance tests.
- Add regression over previous units.

### Phase 4: Module graph

- Add imports.
- Add requires.
- Hash imported context and upstream generated code.
- Re-render affected modules.

### Phase 5: Agent practice

- Add spec-authoring prompts/skills.
- Add healthcheck.
- Add live supervisor.
- Add debug workflow that edits specs only.

## Where To Be Deliberately Simpler Than Codeplain

For a prototype:

- Start with one language and one test framework.
- Skip templates until imports are needed.
- Use one generated folder layout.
- Do not support nested section IDs.
- Use local model calls or one provider API.
- Require explicit file paths in specs.
- Make users run production deployment themselves.

The goal is to prove the loop, not match Codeplain's language.

