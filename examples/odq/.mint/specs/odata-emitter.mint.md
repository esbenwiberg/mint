---
module: odata-emitter
description: Emit OData $filter strings from the query AST
imports: [query-parser]
requires: [query-parser]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: odq-v1
---

## definitions

- OData filter: an OData v4 `$filter` string for the Dataverse Web API.
- EmitError: a typed error for operators or functions the emitter does not support.

## implementation

- Use Python 3.12.
- Expose `compile_filter(text)`, `EmitError`, and the re-exported `LexError` and `ParseError` from `src/odata_emitter/`.
- Parse the input with the required query-parser module, then emit an OData `$filter` string.
- Map comparison operators to OData keywords: `=` to `eq`, `!=` to `ne`, `<` to `lt`, `<=` to `le`, `>` to `gt`, `>=` to `ge`.
- Emit `and`, `or`, and `not (...)`, wrapping each binary group in parentheses so precedence is explicit.
- Escape single quotes inside string literals by doubling them; emit dates and numbers unquoted and booleans as `true` or `false`.
- Support only the functions `contains` and `startswith`; raise `EmitError` for any other function or operator.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public emitter API.
- Include an escaped-literal case and an unknown-function error.

## functional

- id: FR1
  title: Compile comparisons into an OData filter
  spec:
    - `compile_filter("status = 'Active'")` returns `status eq 'Active'`.
    - `compile_filter("enddate < 2026-01-01")` returns `enddate lt 2026-01-01`.
  acceptance:
    - `compile_filter("status = 'Active'")` equals the string `status eq 'Active'`.
    - `compile_filter("enddate < 2026-01-01")` equals the string `enddate lt 2026-01-01`.

- id: FR2
  title: Escape literals and parenthesize boolean logic
  spec:
    - `compile_filter("status = 'Active' and price >= 100")` wraps the two comparisons and joins them with `and`.
    - A string literal containing a single quote is escaped by doubling the quote.
  acceptance:
    - `compile_filter("status = 'Active' and price >= 100")` equals `(status eq 'Active') and (price ge 100)`.
    - `compile_filter("name = 'O''Brien'")` equals `name eq 'O''Brien'`.

- id: FR3
  title: Reject unknown functions and operators
  spec:
    - `compile_filter("endswith(name, 'x')")` raises `EmitError` because only `contains` and `startswith` are supported.
    - The error message names the unsupported function.
  acceptance:
    - `compile_filter("endswith(name, 'x')")` raises `EmitError` whose message contains `endswith`.
    - `compile_filter("startswith(name, 'Ac')")` equals `startswith(name,'Ac')`.
