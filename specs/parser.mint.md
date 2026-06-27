---
module: parser
description: Expression-language parser
imports: [lexer]
requires: [lexer]
stack: python-lib
rendererProvider: model
rendererModel: mint-replay-calc-v1
rendererPromptVersion: calc-v1
---

## definitions

- AST: a typed expression tree.
- ParseError: a syntax error with a clean message.

## implementation

- Use Python 3.12.
- Expose AST node classes, `ParseError`, and `parse(text)` from `src/parser/`.
- Parse numbers, names as function calls, parentheses, comma-separated arguments, and binary operators.
- Operator precedence is `*` and `/` before `+` and `-`; parentheses override precedence.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public parser API.
- Include precedence and syntax error coverage.

## functional

- id: FR1
  title: Parse expressions into AST
  spec:
    - `parse("2 + 3 * 4")` builds an AST where multiplication binds before addition.
    - `parse("add(2, 3)")` builds a call expression with two numeric arguments.
    - Syntax mistakes raise `ParseError`.
  acceptance:
    - `parse("2 + 3 * 4")` produces a Binary `+` root whose right child is Binary `*`.
    - `parse("add(2, 3)")` produces a Call named `add` with two Number arguments.
    - `parse("2 +")` raises `ParseError` with `expected expression` in the message.
