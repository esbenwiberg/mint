---
module: scrub-cli
description: Command-line interface that anonymizes a CSV export
imports: [export-parser, pseudonymizer, writer]
requires: [export-parser, pseudonymizer, writer]
stack: python-cli
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: scrub-v1
---

## definitions

- CLI: the `scrub` command-line interface.
- Config: a JSON file holding the seed and the column-to-PII-type mapping.
- Exit code: the process status returned to the shell.

## implementation

- Use Python 3.12.
- Provide a package `scrub_cli` with `main(argv=None)` and a console command named `scrub`.
- Read an export file path and a config file path as positional command-line arguments.
- The config file holds a `seed` string and a `columns` mapping from column name to PII type.
- Compose the required export-parser, pseudonymizer, and writer modules: parse the export, pseudonymize the mapped columns, then write anonymized CSV to stdout.
- Map a config error to exit code 2, an input or parse error to exit code 3, and a writer error to exit code 4, each with a clean stderr message; exit 0 on success.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests drive the CLI as a subprocess.
- Include a success case and each typed-error exit code.

## functional

- id: FR1
  title: Anonymize an export using a seeded config
  spec:
    - `scrub export.csv config.json` prints anonymized CSV to stdout and exits 0.
    - The same input and seed produce the same anonymized output on every run.
  acceptance:
    - Given a two-row export and a config seeding the `name` and `email` columns, running `python -m scrub_cli.cli export.csv config.json` exits 0 and prints a header line followed by two data rows.
    - Running the command twice with the same input and config prints identical output both times.

- id: FR2
  title: Report typed errors with distinct exit codes
  spec:
    - A missing or invalid config file exits with the config-error code.
    - A malformed export file exits with the input-error code.
  acceptance:
    - Running `python -m scrub_cli.cli export.csv missing.json` exits 2 and prints `config error` on stderr.
    - Running `python -m scrub_cli.cli ragged.csv config.json` exits 3 and prints `input error` on stderr.
