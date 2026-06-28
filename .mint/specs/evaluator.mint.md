---
module: evaluator
description: Expression-language evaluator
imports: [parser]
requires: [parser]
stack: python-lib
rendererProvider: model
rendererModel: mint-replay-calc-v1
rendererPromptVersion: calc-v1
---

## definitions

- EvalError: a typed runtime error for evaluation failures.
- Built-in function: a named function available to expressions.

## implementation

- Use Python 3.12.
- Expose `EvalError` and `evaluate(text)` from `src/evaluator/`.
- Support arithmetic operators, built-in functions, and typed runtime errors.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public evaluator API.
- Include arithmetic, built-ins, divide-by-zero, and unknown-name errors.

## functional

- id: FR1
  title: Evaluate arithmetic expressions
  spec:
    - `evaluate("2 + 3 * 4")` returns `14`.
    - `evaluate("(2 + 3) * 4")` returns `20`.
    - Division by zero raises `EvalError`.
  acceptance:
    - `evaluate("2 + 3 * 4") == 14` and `evaluate("(2 + 3) * 4") == 20`.
    - `evaluate("8 / 0")` raises `EvalError` with `divide by zero` in the message.

- id: FR2
  title: Evaluate built-in functions and unknown names
  spec:
    - Built-in functions include `add`, `sub`, `mul`, `div`, `max`, and `min`.
    - Calling an unknown name raises `EvalError`.
  acceptance:
    - `evaluate("max(add(2, 3), min(10, 4))") == 5`.
    - `evaluate("missing(1)")` raises `EvalError` with `unknown name` in the message.
