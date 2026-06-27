# Capsule

Codeplain's public story is "review specs, not code", but the public repos show
a concrete mechanism behind it.

## Working Model

Codeplain is a two-part system:

- `plain-forge`: an agent instruction pack and installer. It helps coding agents
  write and maintain `.plain` specification files.
- `codeplain` CLI/client: a Python client that parses `.plain`, calls the hosted
  Codeplain API, writes generated code/tests, runs local test scripts, and
  manages partial renders.

The hosted API is proprietary. The local client and spec-authoring practice are
public enough to reverse engineer the pattern.

## Unit Of Regeneration

The atomic unit is a functionality, represented in client code as a functional
requirement ID, or FRID.

Public pricing says one rendering credit maps to one successfully rendered
functionality. The client code agrees: it renders one FRID at a time, commits
after each FRID, and supports `--render-from` / `--render-range`.

## Slice Boundaries

Slicing is decided at spec authoring time:

- `import` pulls in shared definitions, implementation reqs, and test reqs.
  Import modules must not contain functional specs.
- `requires` creates a build chain between functional modules. Required modules
  are built first, and their generated code is copied forward as the starting
  point for dependent modules.
- Functional specs are rendered top to bottom. Later specs are invisible while an
  earlier spec is being rendered.
- Each functional spec should imply at most about 200 changed lines of code.

## Render Loop

The public client runs a state machine:

1. Prepare generated-code and conformance-test Git repos.
2. Render one functional requirement.
3. Run unit tests.
4. Fix unit-test failures, with retry limits.
5. Refactor generated code.
6. Run unit tests again.
7. Generate conformance tests.
8. Prepare the test environment.
9. Run conformance tests.
10. Fix either tests or implementation.
11. Run regression tests for previous FRIDs.
12. Commit a checkpoint for the FRID.

The generated module folders are Git repos. Commit messages store FRID, module
name, and render ID so the client can rollback and resume.

## Change Detection

The client stores module metadata under `.codeplain/module_metadata.json`.

It tracks:

- source hash
- non-functional source hash
- required-module code hash
- rendered functional specs

Partial rendering is only safe when non-functional content is unchanged. If a
functional spec changed, the client finds the earliest affected FRID and can
render from there. If definitions or implementation reqs changed, full affected
module rendering is safer.

## Why This Works Best For Integrations

External API integrations are a strong fit:

- They have natural boundaries.
- They often depend on schemas and OpenAPI resources.
- Upstream APIs break in small ways.
- The desired behavior often remains stable while implementation details change.

The harder case is a tightly coupled app where generated internals become
implicit contracts. The discipline is to depend only on spec-defined interfaces
and conformance tests, not on generated file layout or incidental functions.

