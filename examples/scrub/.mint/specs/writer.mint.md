---
module: writer
description: Serialize anonymized rows back into CSV text
imports: [export-parser]
requires: [export-parser]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: scrub-v1
---

## definitions

- Output export: the anonymized CSV text produced from pseudonymized rows.
- WriterError: a typed error for a row that does not match the header.

## implementation

- Use Python 3.12.
- Expose `write_export(header, rows)` and `WriterError` from `src/writer/`.
- Serialize the header and the rows, using the required export-parser Row shape, into CSV text with each line terminated by a newline.
- Emit columns in header order for every row.
- Quote any value that contains a comma, a double quote, or a newline, following standard CSV quoting rules.
- Raise `WriterError` when a row is missing a column named in the header.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public API.
- Include a quoting case and a missing-column error.

## functional

- id: FR1
  title: Serialize rows to CSV text
  spec:
    - `write_export(["name", "email"], [{"name": "Nova", "email": "nova@example.test"}])` returns CSV text with a header line and one data line.
    - Columns are emitted in header order.
  acceptance:
    - `write_export(["name", "email"], [{"name": "Nova", "email": "nova@example.test"}])` returns the text `name,email` then `Nova,nova@example.test`, each on its own line.
    - The first output line equals `name,email`.

- id: FR2
  title: Quote special values and reject incomplete rows
  spec:
    - A value containing a comma is wrapped in double quotes in the output.
    - A row missing a header column raises `WriterError`.
  acceptance:
    - `write_export(["name"], [{"name": "Ada, Jr."}])` emits the data line `"Ada, Jr."` with surrounding double quotes.
    - `write_export(["name", "email"], [{"name": "Nova"}])` raises `WriterError` whose message contains `missing column`.
