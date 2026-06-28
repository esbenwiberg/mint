# Release and CI

## Package metadata

The v1 package is installable with:

```bash
python -m pip install -e .
```

Development installs use pinned test tooling:

```bash
python -m pip install -e ".[dev]"
```

The release version is recorded in both `pyproject.toml` and `VERSION`, and exposed
as `mint_cli.__version__`.

## Offline CI

`.github/workflows/offline-ci.yml` runs on push and pull request with Python 3.12
and Node 22, with no secrets:

```bash
python -m pip install -e ".[dev]"
mint doctor
pytest --cov=mint_cli --cov-report=term-missing:skip-covered --cov-fail-under=80 -q
```

`MINT_LIVE=0` is set explicitly. Model specs use `ReplayClient` and the committed
cassettes under `resources/cassettes/v1/`.

The default pytest run includes `tests/e2e/`. Those tests launch `mint` in
subprocesses against isolated project directories, render a demo dependency graph,
and verify the status/report/inspect/clean workflow through the public CLI.
Offline CI also enforces an 80% package coverage floor. CLI subprocess execution is
covered behaviorally by e2e tests, even though those child processes are not counted
as line coverage in the default coverage report.

Generated test scripts run pytest through `PYTHON_BIN`. The workflow defaults
`PYTHON_BIN` to the interpreter running `mint`, so generated unit, conformance, and
test-quality checks stay on the same Python runtime unless the caller explicitly
overrides it.

TypeScript stack tests run npm-compatible scripts through local tool stubs in the
pytest suite, proving Mint invokes `tsc --noEmit` and Vitest without downloading
npm dependencies during offline CI.

## Live recording

`.github/workflows/live-record.yml` is manual-only (`workflow_dispatch`). It sets
`MINT_LIVE=1`, requires `ANTHROPIC_API_KEY`, installs the `live` optional extra, and
runs:

```bash
mint live-smoke <module>
```

This job is intentionally separate from default CI so routine validation never needs
network access or provider credentials.
