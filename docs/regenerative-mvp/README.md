# Regenerative MVP

This folder defines the first implementable version of a local
Codeplain-inspired regenerative coding system for this repo.

The design is grounded in [../codeplain-research](../codeplain-research/):
Codeplain keeps specs as source, treats generated code as disposable output, and
uses small functional units, test gates, metadata, Git checkpoints, and a render
supervisor to make regeneration mechanical.

## Documents

1. [Purpose](./purpose.md) - what the MVP is trying to prove.
2. [Architecture](./architecture.md) - system components and major decisions.
3. [Data Model](./data-model.md) - spec IR, metadata, checkpoints, and logs.
4. [CLI Workflow](./cli-workflow.md) - commands and user-facing flows.
5. [Implementation Briefs](./implementation-briefs.md) - phased build plan.
6. [Decision Log](./decision-log.md) - tradeoffs from the subagent council.

## One-Screen Summary

The MVP is a local render loop named `mint`:

- `mint` itself is a Python 3.12 CLI.
- Human-facing specs are Markdown files with YAML frontmatter.
- The parser turns those specs into a structured internal IR.
- Each functional unit has a stable ID and is rendered in chronological order.
- The first renderer is a deterministic local Python CLI/library adapter.
- Generated code lives under `generated/<module>/`.
- Each generated module is its own Git repo with checkpoints per unit.
- Metadata lives at `generated/<module>/.mintgen/module.json`.
- Unit tests are the fast inner gate.
- Black-box conformance tests are the behavior gate.
- Healthcheck validates spec/config/script readiness before a render.
- Agent authoring skills edit specs and scripts, not generated code.

The first implementation supports one module, one generated target stack
(Python CLI/library + pytest), one generated folder, one unit/conformance test
command pair, and explicit acceptance tests. Model-backed rendering, imports,
requires, non-Python targets, full Plain compatibility, and rich golden fixture
management come later.
