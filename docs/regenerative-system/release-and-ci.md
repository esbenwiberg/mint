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

`.github/workflows/offline-ci.yml` runs on push and pull request with no secrets:

```bash
python -m pip install -e ".[dev]"
mint doctor
pytest -q
```

`MINT_LIVE=0` is set explicitly. Model specs use `ReplayClient` and the committed
cassettes under `resources/cassettes/v1/`.

## Live recording

`.github/workflows/live-record.yml` is manual-only (`workflow_dispatch`). It sets
`MINT_LIVE=1`, requires `ANTHROPIC_API_KEY`, installs the `live` optional extra, and
runs:

```bash
mint live-smoke <module>
```

This job is intentionally separate from default CI so routine validation never needs
network access or provider credentials.
