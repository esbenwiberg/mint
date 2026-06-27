# Decision Log

This summarizes the architecture council pass run from the Codeplain research
pack.

## D1: Spec Format

Decision: use simplified Markdown plus YAML frontmatter as the MVP authoring
surface, with a canonical JSON IR underneath.

Rejected for MVP:

- full Plain compatibility
- pure YAML/JSON as the human-facing spec

Why:

- Markdown keeps authoring ergonomic for humans and agents.
- The IR keeps parsing deterministic.
- Full Plain compatibility can come later as import/export once the render loop
  is proven.

Risk:

- The simplified format must still preserve the hard parts: chronological units,
  small slices, acceptance tests, local resources, and non-functional invalidation.

## D2: Checkpoints

Decision: initialize a Git repo inside each `generated/<module>` folder and
store metadata under `generated/<module>/.mintgen/module.json`.

Rejected for MVP:

- custom snapshot store
- using main repo commits as render checkpoints

Why:

- Git already solves diff, rollback, and checkpoint search.
- Nested generated repos isolate disposable output from the main repo.
- Codeplain's public client uses generated folders as Git repos for similar
  reasons.

Risk:

- Nested Git repos can confuse tooling. Document that `generated/*` is
  renderer-owned.

## D3: Render Loop

Decision: implement code plus unit tests first, then generate separate
conformance tests in the same MVP.

Rejected for MVP:

- unit tests only as the final proof
- full Codeplain state machine from day one

Why:

- Unit tests alone are fast but do not prove behavior.
- Conformance tests are the behavior gate and are central to the regenerative
  claim.
- The full Codeplain loop has too much machinery for the first implementation.

Risk:

- Conformance generation adds scope. Keep it small: one module, one stack, one
  retry, explicit stop conditions.

## D4: Test Gates

Decision: use black-box unit and conformance scripts with exit-code contracts.
Acceptance tests from the spec become additional conformance scenarios.

Rejected for MVP:

- unit tests only
- full golden fixture/contract registry as default

Why:

- Scripts are language-agnostic from the renderer's perspective.
- Stdout/stderr become feedback for retries.
- Golden fixtures are valuable for integrations, but not required for the first
  loop.

Risk:

- Bad scripts create false confidence. Healthcheck must validate script presence
  and executability, and debugging must treat scripts as possible root causes.

## D5: Authoring Workflow

Decision: create focused Codex skills/prompts for:

- `add-feature`
- `debug-specs`
- `healthcheck`
- `render-supervisor`

Rejected for MVP:

- docs-only conventions
- full plain-forge-like skill pack immediately

Why:

- The research shows authoring discipline is part of the system, not a nice
  extra.
- A small focused skill set gets the one-question loop and generated-code
  read-only rule into the workflow without copying every plain-forge skill.

Risk:

- If skills are too vague, agents will drift back to editing code. Keep the
  skills procedural and tied to `mint` commands.

## D6: First Stack

Decision: implement `mint` as a Python 3.12 CLI and target generated Python
CLI/library packages with pytest for the first render loop.

Rejected for MVP:

- starting with FastAPI
- starting with TypeScript
- designing a stack-agnostic adapter system first

Why:

- Python gives the shortest path to a local CLI, parser, subprocess scripts,
  pytest gates, and nested Git operations.
- CLI/library generated targets keep conformance black-box without needing a
  server lifecycle or browser runtime.
- A single concrete stack makes the first two-unit regeneration test crisp.

Risk:

- The design may overfit Python. Keep the script contracts stack-neutral so
  future adapters do not require rewriting the supervisor.

## D7: First Renderer

Decision: prove v1 with a deterministic local renderer for the Python
CLI/library target before adding a model-backed renderer.

Rejected for v1:

- requiring OpenAI or another hosted model to run the example
- treating arbitrary code generation as the first acceptance test

Why:

- The core claim to prove first is regeneration discipline: slices, checkpoints,
  metadata, gates, no-op detection, and `--from` rerendering.
- A deterministic renderer makes the proof repeatable in CI and local
  development without credentials or network access.
- The renderer boundary remains pluggable, so a model adapter can replace the
  local adapter later without changing parser, metadata, or gate semantics.

Risk:

- This does not prove model quality. It proves the machinery around generated
  code is sound enough to host a model-backed renderer later.

## MVP Bias

When in doubt, choose:

- one stack
- one module
- one generated folder
- explicit unit IDs
- explicit acceptance tests
- script gates over framework assumptions
- specs/scripts over generated-code edits
