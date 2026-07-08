---
module: query-lexer
description: Query DSL lexer for the odq OData compiler
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: odq-v1
---

## definitions

- Token: a typed lexical item with a value.
- Literal: a string, number, date, or boolean value in the query DSL.

## implementation

- Use Python 3.12.
- Expose `Token`, `LexError`, and `tokenize(text)` from `src/query_lexer/`.
- Tokenize identifiers, the comparison operators `=`, `!=`, `<`, `<=`, `>`, `>=`, the keywords `and`, `or`, `not`, parentheses, commas, and function names.
- Recognize single-quoted string literals, integer and decimal numbers, ISO dates written `YYYY-MM-DD`, and the booleans `true` and `false`.
- Raise `LexError` for an unterminated string, a malformed date, or an unexpected character.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public lexer API.
- Include a valid token stream and bad-literal errors.

## functional

- id: FR1
  title: Tokenize a query string
  spec:
    - `tokenize("status = 'Active'")` returns tokens for an identifier, an equals operator, a string literal, and end-of-input.
    - Whitespace between tokens is ignored.
  acceptance:
    - `tokenize("status = 'Active'")` emits IDENT `status`, OP `=`, STRING `Active`, and EOF in that order.
    - `tokenize("a and b")` emits IDENT `a`, AND, IDENT `b`, and EOF in that order.

- id: FR2
  title: Reject bad literals
  spec:
    - An unterminated string literal raises `LexError`.
    - A malformed date raises `LexError`.
  acceptance:
    - `tokenize("name = 'oops")` raises `LexError` whose message contains `unterminated string`.
    - `tokenize("d = 2026-13-40")` raises `LexError` whose message contains `invalid date`.
