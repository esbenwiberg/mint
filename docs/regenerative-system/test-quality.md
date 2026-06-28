# Test-quality gate

The unit and conformance gates prove that generated tests pass. The test-quality
gate checks that those tests are not shallow. Today the full gate applies to Python
stacks; TypeScript stacks record a clean skipped verdict until TS coverage and
mutation support is added.

It runs after unit and conformance tests pass, before the unit checkpoint is
committed. A failure stops the render and records the verdict in both the attempt
manifest and `module.json`.

## Checks

### Coverage threshold

For Python stacks, `mint` runs the generated unit tests and conformance tests under
a line tracer in separate subprocesses, then merges covered line numbers for files
under `generated/<module>/src/`.

Generated provenance files are excluded. The default threshold is 60 percent.

### Acceptance traceability

Every acceptance criterion must be referenced by tests. The gate tokenizes each
criterion and the generated test files, then requires at least two distinctive
criterion tokens to appear in tests. This catches placeholder tests such as
`assert True` even when unit and conformance commands are green.

### Mutation probe

The mutation probe temporarily replaces public generated function or method bodies
with:

```python
raise AssertionError("mint mutation probe: <name>")
```

It then re-runs unit and conformance scripts. If tests still pass, the generated
test suite is too weak and the gate fails. Source files are restored before the
render continues or reports failure.

This is intentionally lightweight, not full mutation testing. By default it tests
up to three public candidates per unit.

## Configuration

`mint.yaml`:

```yaml
testQuality:
  enabled: true
  minCoveragePercent: 60
  mutationProbe: true
  mutationMaxCandidates: 3
```

The gate is enabled by default even when this section is omitted.

For `typescript-lib` and `typescript-node`, the attempt manifest stores
`"status": "skipped"` with reason `test-quality is not implemented for <stack> yet`.
The unit and conformance gates still run through `tsc --noEmit` and Vitest before
that skipped verdict is recorded.

## Metadata

Attempt manifest:

```text
generated/<module>/.mintgen/attempts/<unit>/test-quality-1.json
```

The manifest stores `classification` (`passed` or `test_quality_failed`) and a
`testQuality` object with coverage, traceability, and mutation details.

`module.json` stores the same verdict on the functional-unit record:

```json
{
  "id": "FR1",
  "status": "passed",
  "attempts": {
    "unit": 1,
    "conformance": 1,
    "testQuality": 1
  },
  "testQuality": {
    "status": "passed"
  }
}
```

`mint report <module>` includes the per-unit `testQuality` verdict.
