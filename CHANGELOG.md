# Changelog

## Unreleased

- Added `mint prune [--yes]`: deletes replay cassettes no rendered module
  references. Refuses while any module's generated output is stale or missing
  (the live cassette set is only knowable from an up-to-date render's attempt
  manifests), lists orphans dry-run by default, and warns when a referenced
  cassette is missing from `resources/cassettes`.
- Added `docs/spec-authoring.md`: spec-writing guide covering testable
  acceptance, blast-radius-aware section placement, the UI style-lock pattern,
  and the record/replay/prune workflow.

## 1.0.1 - 2026-07-07

- Fixed replay cassettes recording absolute machine paths into prompts, which made
  offline replay fail on any other machine or checkout path (GH #1). Retry
  feedback is now scrubbed of paths, durations, timestamps, memory addresses, and
  pytest-xdist worker ids before it is hashed or stored, and a regression test
  asserts rendered prompts never contain the project root.
- Fixed partial renders poisoning later runs; renders of the same module are now
  serialized with a per-module advisory lock.
- Fixed config errors surfacing far downstream instead of at the offending
  `mint.yaml` line.
- Fixed TypeScript render gates: dependencies are installed before scripts run,
  subprocesses get timeouts, and gate ordering was rebalanced.
- Added explicit request timeout and retry count to the live Anthropic client
  (env-overridable).
- Added a pip probe to `mint doctor`: an interpreter without `pip` (e.g. a bare
  uv venv) now fails doctor with a fix hint instead of failing the first render's
  prepare step.
- Hardened the live-record CI workflow against dispatch-input injection and made
  it persist refreshed cassettes; the package version is single-sourced from the
  `VERSION` file.
- Removed the legacy top-level `generated/` placeholder directory; generated
  output lives under `.mint/generated/`.

## 1.0.0 - 2026-06-27

- Added record/replay model cassettes with offline replay by default and live
  recording behind `MINT_LIVE=1`.
- Added template-free replayed calc graph: lexer -> parser -> evaluator -> calc-cli.
- Added test-quality gate for coverage, acceptance traceability, and mutation probes.
- Added run reports, attempt/token budget aborts, and resume-from-checkpoint behavior.
- Added `mint new`, `mint lint`, `mint doctor`, and `mint report`.
- Added v1 docs for record/replay, test quality, cost/budgets, and commands.
