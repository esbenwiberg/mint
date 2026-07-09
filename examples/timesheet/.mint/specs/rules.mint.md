---
module: rules
description: Validation rules and approval state machine for timesheet entries
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- Status: one of `draft`, `submitted`, `approved`, `rejected`.
- Day total: the sum of hours a person already has registered on one calendar date.
- RuleError: the typed error raised for an invalid value or an illegal status transition.

## implementation

- Use Python 3.12 with pure functions only — no I/O, no module state, no third-party dependencies.
- Expose `RuleError`, `validate_hours(hours, day_total=0.0)`, `transition(current, target)`, and `assert_editable(status)` from `src/rules/`.
- Every rule check raises `RuleError` at call time; nothing is validated at import time.
- `validate_hours` returns None for positive hours whose new day total stays within the 24-hour day; it raises `RuleError` for zero or negative hours, or when the new day total would exceed 24.
- `transition` returns the target status for exactly three legal moves — `draft` to `submitted`, `submitted` to `approved`, `submitted` to `rejected` — and raises `RuleError` for every other pair, including unknown status names.
- `assert_editable` returns None for `draft`, `submitted`, and `rejected`; it raises `RuleError` for `approved`, because approved entries are immutable.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public API.
- Include the exact 24-hour day boundary, a zero-hours rejection, and illegal transitions from every status.

## functional

- id: FR1
  title: Validate hours against the daily cap
  spec:
    - `validate_hours(hours, day_total=0.0)` returns None when `hours > 0` and `day_total + hours <= 24`.
    - It raises `RuleError` at call time when `hours <= 0` or when `day_total + hours` exceeds 24.
  acceptance:
    - `validate_hours(8.0)` returns None.
    - `validate_hours(10.0, day_total=14.0)` returns None — the new day total equals the exact 24-hour boundary and is accepted.
    - `validate_hours(10.5, day_total=14.0)` raises `RuleError` whose message contains `24`.
    - `validate_hours(0)` raises `RuleError` whose message contains `positive`.
    - `validate_hours(-1.5)` raises `RuleError`.

- id: FR2
  title: Approval state machine
  spec:
    - `transition(current, target)` returns the target status for the legal moves `draft` to `submitted`, `submitted` to `approved`, and `submitted` to `rejected`.
    - Every other pair — skipping a step, moving backwards, leaving a terminal status, or an unknown status name — raises `RuleError` at call time.
  acceptance:
    - `transition("draft", "submitted")` returns `submitted`.
    - `transition("submitted", "approved")` returns `approved`.
    - `transition("submitted", "rejected")` returns `rejected`.
    - `transition("draft", "approved")` raises `RuleError` whose message contains `illegal transition`.
    - `transition("approved", "submitted")` raises `RuleError`.
    - `transition("draft", "vacation")` raises `RuleError` whose message contains `unknown status`.

- id: FR3
  title: Approved entries are immutable
  spec:
    - `assert_editable(status)` returns None for `draft`, `submitted`, and `rejected`.
    - `assert_editable("approved")` raises `RuleError` at call time.
  acceptance:
    - `assert_editable("draft")` returns None.
    - `assert_editable("rejected")` returns None.
    - `assert_editable("approved")` raises `RuleError` whose message contains `immutable`.
