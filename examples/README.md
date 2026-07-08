# Mint examples

Three small, self-contained example tools built as Mint spec graphs:

- [`odq/`](./odq) — a mini query DSL that compiles to OData `$filter` strings for
  the Dataverse Web API.
- [`scrub/`](./scrub) — a deterministic, seeded project-data anonymizer for CSV
  exports.
- [`timesheet/`](./timesheet) — a timesheet backend (FastAPI) + CLI with a
  running-timer workflow, plus hand-written Claude Code hooks that meter coding
  sessions into it.

Each is written in the same style as the repo's `calc-cli` graph: model-backed
`*.mint.md` specs, rendered one functional unit at a time, with typed errors and
distinct exit codes.

## Why each example is its own Mint project

Every example folder is a **separate Mint project** with its own `mint.yaml`,
`.mint/specs/`, `.mint/generated/`, `conformance/`, `resources/`, and
`test_scripts/` — created with `mint init --write`. This is deliberate:

- **Spec discovery is flat and per-project.** Mint resolves a module at
  `<project>/<specsDir>/<module>.mint.md` and discovers specs with a
  non-recursive `*.mint.md` glob (see `doctor`/`next`). Nesting the examples
  under one shared `.mint/specs` would force all twelve modules into the root
  project's single flat namespace.
- **Isolation of state.** Cassettes (`resources/cassettes/`), generated output,
  and conformance tests all resolve relative to the directory holding
  `mint.yaml`. Separate projects keep each example's record/replay fixtures and
  build artifacts from colliding with the root project or with each other.
- **The root project stays stable.** `mint doctor` parses and gates *every* spec
  in a project. Dropping unrendered model specs into the repo root would make the
  root `doctor` (and CI) fail on missing cassettes. Per-folder projects keep the
  examples runnable and gated on their own terms.

Trade-off: each folder duplicates `mint.yaml` and the three `test_scripts/*.sh`.
That duplication is exactly what `mint init --write` produces for any new repo,
so the cost is low and each example stays runnable in isolation. If these ever
need to share one config, the alternative is to flatten all specs into the root
`.mint/specs` and accept a single shared module namespace and cassette store.

## Status: recorded and green — `mint render` replays offline

All three graphs are fully recorded and verified:

- `mint lint <module>` and `mint doctor` — **pass** for all twelve modules.
- `mint healthcheck <module>` — **pass** offline; replay cassettes are committed
  under each project's `resources/cassettes/`.
- `mint render <module>` — replays offline (no provider, no network). The built
  CLIs are verified end-to-end (see each tool's README).

The cassettes were recorded once against the `claude-cli` provider (model
`sonnet`). You do not need to record anything to use these examples — just
`mint render`.

### Re-recording after a spec change

Editing a spec changes the render prompt, so its cassettes go stale and
`mint render` will ask you to re-record. That step is manual by design (it calls
a real model provider):

```bash
MINT_LIVE=1 mint render <module> --range FR1:FR1   # one unit at a time, or
MINT_LIVE=1 mint live-smoke <module>               # force a full re-record
```

The specs default to `claude-cli` / `sonnet` (uses your Claude Code auth — no API
key needed). To record with the Anthropic API instead, set each spec's frontmatter
to `rendererProvider: anthropic` / `rendererModel: <model-id>`, install the `live`
extra, and export `ANTHROPIC_API_KEY`.
