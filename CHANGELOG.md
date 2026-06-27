# Changelog

## 1.0.0 - 2026-06-27

- Added record/replay model cassettes with offline replay by default and live
  recording behind `MINT_LIVE=1`.
- Added template-free replayed calc graph: lexer -> parser -> evaluator -> calc-cli.
- Added test-quality gate for coverage, acceptance traceability, and mutation probes.
- Added run reports, attempt/token budget aborts, and resume-from-checkpoint behavior.
- Added `mint new`, `mint lint`, `mint doctor`, and `mint report`.
- Added v1 docs for record/replay, test quality, cost/budgets, and commands.
