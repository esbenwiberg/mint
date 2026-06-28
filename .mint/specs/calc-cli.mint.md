---
module: calc-cli
description: Expression-language calculator CLI
imports: [evaluator]
requires: [evaluator]
stack: python-cli
rendererProvider: model
rendererModel: mint-replay-calc-v1
rendererPromptVersion: calc-v1
---

## definitions

- CLI: the `calc-cli` command-line interface.
- Exit code: process status returned to the shell.

## implementation

- Use Python 3.12.
- Provide a package `calc_cli` with `main(argv=None)`.
- Evaluate one expression argument via the required evaluator module.
- Print the numeric result on success.
- Surface syntax error, unknown name, and divide by zero as clean stderr messages with non-zero exit codes.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests drive the CLI as a subprocess.
- Include success and typed error cases.

## functional

- id: FR1
  title: CLI evaluates expressions and reports typed errors
  spec:
    - `calc-cli "2 + 3 * 4"` prints `14` and exits 0.
    - Divide by zero, unknown names, and syntax errors print clean messages and exit non-zero.
  acceptance:
    - Running `python -m calc_cli.cli "2 + 3 * 4"` prints `14` on stdout and exits 0.
    - Running `python -m calc_cli.cli "8 / 0"` exits 3 and prints `divide by zero` on stderr.
    - Running `python -m calc_cli.cli "missing(1)"` exits 4 and prints `unknown name` on stderr.
    - Running `python -m calc_cli.cli "2 +"` exits 2 and prints `syntax error` on stderr.
