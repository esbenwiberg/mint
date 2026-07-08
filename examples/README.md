# Mint examples

Two small, self-contained example tools built as Mint spec graphs:

- [`odq/`](./odq) — a mini query DSL that compiles to OData `$filter` strings for
  the Dataverse Web API.
- [`scrub/`](./scrub) — a deterministic, seeded project-data anonymizer for CSV
  exports.

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
  under one shared `.mint/specs` would force all eight modules into the root
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

## Status: specs + lint + healthcheck green; render needs a one-time live record

The specs are complete and validated offline:

- `mint lint <module>` — **passes** for all eight modules.
- `mint healthcheck <module>` — green on spec parse, stack, scripts, imports, and
  transitive build order. The **only** reported failure is *"Replay cassettes
  missing"*, because these are template-free model specs with no recorded
  cassettes yet. `MINT_LIVE=1 mint healthcheck <module>` passes with no failures,
  confirming that recording is the sole remaining step.

Ordinary `mint render` runs offline by replaying cassettes. New specs have none,
so rendering requires a **one-time live recording** per project. That step is
manual by design (it calls a real model provider) — see each tool's README for
the exact bottom-up commands. After recording once, `mint render` replays
offline and the cassettes are committed alongside the specs.

### Recording provider

The specs default to the `claude-cli` provider with model `sonnet`, so recording
uses your existing Claude Code auth — no API key or extra install needed. To
record with the Anthropic API instead, edit each spec's frontmatter to
`rendererProvider: anthropic` / `rendererModel: <model-id>`, install the `live`
extra, and export `ANTHROPIC_API_KEY`.
