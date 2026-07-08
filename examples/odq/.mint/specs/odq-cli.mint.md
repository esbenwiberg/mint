---
module: odq-cli
description: Command-line interface that compiles the query DSL to OData
imports: [odata-emitter]
requires: [odata-emitter]
stack: python-cli
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: odq-v1
---

## definitions

- CLI: the `odq` command-line interface.
- Exit code: the process status returned to the shell.

## implementation

- Use Python 3.12.
- Provide a package `odq_cli` with `main(argv=None)` and a console command named `odq`.
- Compile one query argument into an OData `$filter` string via the required odata-emitter module.
- Print the compiled `$filter` string on stdout and exit 0 on success.
- Map a bad literal (`LexError`) to exit code 4, a syntax error (`ParseError`) to exit code 2, and an unknown operator or function (`EmitError`) to exit code 3, each with a clean stderr message.
- Unit tests use pytest.
- Unit tests live in `tests/` and are named `test_*.py` so pytest discovers them; every functional unit ships at least one unit test.

## test

- Conformance tests use pytest.
- Conformance tests drive the CLI as a subprocess.
- Include a success case and each typed-error exit code.
- Write only the current unit's conformance test, at the path `FRn/...` (for example `FR1/test_fr1.py`): the conformance patch root is already this module, so do not add a `tests/` or module-name prefix, and do not create or modify earlier units' conformance tests.

## functional

- id: FR1
  title: Compile a query and report typed errors
  spec:
    - `odq "status = 'Active' and enddate < 2026-01-01"` prints the OData filter and exits 0.
    - Bad literals, syntax errors, and unknown functions print clean messages and exit with distinct non-zero codes.
  acceptance:
    - Running `python -m odq_cli.cli "status = 'Active' and enddate < 2026-01-01"` prints `(status eq 'Active') and (enddate lt 2026-01-01)` on stdout and exits 0.
    - Running `python -m odq_cli.cli "name = 'oops"` exits 4 and prints `bad literal` on stderr.
    - Running `python -m odq_cli.cli "status ="` exits 2 and prints `syntax error` on stderr.
    - Running `python -m odq_cli.cli "endswith(name, 'x')"` exits 3 and prints `unknown operator` on stderr.
