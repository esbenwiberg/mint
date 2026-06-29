# Cost and budgets

`mint` tracks render attempts and estimated prompt/response tokens as part of the
run report. Budget limits live in `mint.yaml`.

```yaml
limits:
  maxRenderAttempts: 0
  maxRenderTokensEstimate: 0
```

`0` means unlimited. Any positive value is enforced during the render.

## Attempt budget

Every persisted attempt counts against `maxRenderAttempts`:

- unit attempts
- conformance attempts
- patch-validation failures
- test-quality attempts

When the limit is exceeded, rendering aborts with a message naming the unit, phase,
attempt, and the fix hint.

## Token budget

`maxRenderTokensEstimate` uses the same simple estimator as `mint report`: roughly
one token per four characters of recorded prompt plus response text. This is an
estimate, not provider billing truth, but it gives CI and local runs a deterministic
guardrail before a runaway render burns through a large cassette/live-model budget.

## Abort report

Budget aborts write:

```text
.mint/generated/<module>/.mintgen/reports/budget-abort.json
```

The report includes module, unit id, phase, attempt, reason, attempts used, token
estimate used, and the configured max values.

## Cost estimate

`mint report <module>` currently sets `costEstimateUsd` to `0.0` and marks pricing as
unconfigured. Token counts are deterministic estimates so offline replay and CI can
reason about budget shape without needing provider price tables or network access.
