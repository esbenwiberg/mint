---
module: lexer
description: Expression-language lexer
imports: []
requires: []
stack: python-lib
rendererProvider: model
rendererModel: mint-replay-calc-v1
rendererPromptVersion: calc-v1
---

## definitions

- Token: a typed lexical item with a value.
- Number: an integer or decimal literal.
- Name: an identifier used for built-in function calls.

## implementation

- Use Python 3.12.
- Expose `Token`, `LexerError`, and `tokenize(text)` from `src/lexer/`.
- Tokenize numbers, names, parentheses, commas, and operators `+ - * /`.
- Raise `LexerError` with a clear message for unexpected characters.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public lexer API.
- Include normal token streams and invalid-character errors.

## functional

- id: FR1
  title: Tokenize expression source
  spec:
    - `tokenize("add(2, 3) * 4")` returns tokens for a name, parentheses, numbers, comma, operator, and EOF.
    - Whitespace is ignored.
    - Unexpected characters raise `LexerError`.
  acceptance:
    - `tokenize("add(2, 3) * 4")` includes NAME `add`, NUMBER `2`, NUMBER `3`, STAR `*`, NUMBER `4`, and EOF in order.
    - `tokenize("2 @ 3")` raises `LexerError` with `unexpected character` in the message.
