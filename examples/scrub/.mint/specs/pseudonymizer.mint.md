---
module: pseudonymizer
description: Deterministic seeded pseudonymizer for PII columns
imports: [export-parser]
requires: [export-parser]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: scrub-v1
---

## definitions

- Pseudonym: a deterministic fake value that replaces a real PII value.
- Referential integrity: the property that one input value always maps to the same pseudonym.
- PseudonymizerError: a typed error for an unsupported PII type.

## implementation

- Use Python 3.12.
- Expose `Pseudonymizer` and `PseudonymizerError` from `src/pseudonymizer/`.
- Construct `Pseudonymizer(seed, mapping)` where `mapping` names each column's PII type as `name`, `email`, or `rate`.
- `pseudonymize_value(column, value)` derives the fake value deterministically from the seed and the input value, so equal inputs yield equal outputs and a different seed yields a different output.
- `pseudonymize_rows(rows)` replaces only the mapped columns and passes other columns through unchanged, operating on the required export-parser Row shape.
- Map names to fake names, emails to fake `@example.test` addresses, and rates to stable fake numbers; raise `PseudonymizerError` for an unknown PII type.
- Unit tests use pytest.
- Unit tests live in `tests/` and are named `test_*.py` so pytest discovers them; every functional unit ships at least one unit test.

## test

- Conformance tests use pytest.
- Conformance tests call only the public API.
- Include a determinism check, a referential-integrity check, and an unknown-type error.
- Write only the current unit's conformance test, at the path `FRn/...` (for example `FR1/test_fr1.py`): the conformance patch root is already this module, so do not add a `tests/` or module-name prefix, and do not create or modify earlier units' conformance tests.

## functional

- id: FR1
  title: Pseudonymize values deterministically
  spec:
    - With a fixed seed, `pseudonymize_value("name", "Ada")` returns the same pseudonym on every call.
    - The pseudonym for a value differs from the original value.
  acceptance:
    - With seed `demo-seed`, two calls to `pseudonymize_value("name", "Ada")` return equal strings.
    - `pseudonymize_value("name", "Ada")` returns a string that does not equal `Ada`.

- id: FR2
  title: Preserve referential integrity and pass through unmapped columns
  spec:
    - The same input value maps to the same pseudonym across different rows.
    - Columns absent from the mapping are copied through unchanged.
  acceptance:
    - Two rows whose `email` is `ada@example.test` pseudonymize so that both output emails are equal.
    - A `project` column absent from the mapping is copied so its output value equals its input value.

- id: FR3
  title: Reject unknown PII types
  spec:
    - Declaring a mapping with an unsupported PII type raises `PseudonymizerError`.
  acceptance:
    - `Pseudonymizer("demo-seed", {"ssn": "government-id"})` raises `PseudonymizerError` whose message contains `unknown PII type`.
