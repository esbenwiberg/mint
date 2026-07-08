---
module: query-parser
description: Query DSL parser that builds a filter AST
imports: [query-lexer]
requires: [query-lexer]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: odq-v1
---

## definitions

- AST: a typed tree of filter expressions.
- ParseError: a syntax error with a clean message.

## implementation

- Use Python 3.12.
- Expose AST node classes, `ParseError`, and `parse(text)` from `src/query_parser/`.
- Parse comparisons, the boolean operators `and`, `or`, and `not`, parentheses, and the function calls `contains` and `startswith`.
- Precedence binds `not` tighter than `and`, and `and` tighter than `or`; parentheses override precedence.
- Build the tree from tokens produced by the required query-lexer module.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public parser API.
- Include precedence coverage and a syntax-error case.

## functional

- id: FR1
  title: Parse comparisons and boolean structure
  spec:
    - `parse("status = 'Active' and price > 100")` builds an And node over two comparisons.
    - `parse("a = 1 or b = 2 and c = 3")` groups the `and` beneath the `or` by precedence.
  acceptance:
    - `parse("status = 'Active' and price > 100")` returns an And whose left child compares `status` and whose right child compares `price` against `100`.
    - In `parse("a = 1 or b = 2 and c = 3")`, the root node is Or and its right child is And.

- id: FR2
  title: Parse functions and report syntax errors
  spec:
    - `parse("contains(name, 'abc')")` builds a Call node named `contains` with a field argument and a string argument.
    - A comparison missing its right-hand side raises `ParseError`.
    - Input with trailing tokens after a complete expression raises `ParseError`.
  acceptance:
    - `parse("contains(name, 'abc')")` returns a Call whose name equals `contains` with arguments `name` and `abc`.
    - `parse("status =")` raises `ParseError` whose message contains `expected`.
    - `parse("a = 1 b")` raises `ParseError` whose message contains `end of input`.
