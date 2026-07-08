---
module: export-parser
description: Parse a CSV project/resource export into ordered rows
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: scrub-v1
---

## definitions

- Export: a CSV export of project and resource data with a header row.
- Row: an ordered mapping from column name to string value.
- ParseError: a typed error for malformed CSV input.

## implementation

- Use Python 3.12.
- Expose `parse_export(text)`, `ParseError`, and a `Row` type alias from `src/export_parser/`.
- Parse the header row, then return data rows as ordered mappings from column name to string value.
- Preserve column order and row order exactly as they appear in the input.
- Raise `ParseError` when a data row has a different column count than the header, or when the input has no header.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public parser API.
- Include a well-formed export and a ragged-row error.

## functional

- id: FR1
  title: Parse a CSV export into ordered rows
  spec:
    - `parse_export("name,email\nAda,ada@example.test")` returns one row mapping `name` to `Ada` and `email` to `ada@example.test`.
    - Header column order is preserved in every row.
  acceptance:
    - `parse_export("name,email\nAda,ada@example.test")` returns a list whose single row equals `{"name": "Ada", "email": "ada@example.test"}`.
    - The parsed header equals `["name", "email"]` in that order.

- id: FR2
  title: Reject malformed exports
  spec:
    - A data row with more or fewer fields than the header raises `ParseError`.
    - Input with no header row raises `ParseError`.
  acceptance:
    - `parse_export("name,email\nAda")` raises `ParseError` whose message contains `column count`.
    - `parse_export("")` raises `ParseError` whose message contains `no header`.
