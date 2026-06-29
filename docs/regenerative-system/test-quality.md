# Test-quality gate

The unit and conformance gates prove that generated tests pass. The test-quality
gate checks that those tests are not shallow. Python and TypeScript stacks both run
the gate; stack adapters own the coverage and mutation mechanics.

It runs after unit and conformance tests pass, before the unit checkpoint is
committed. A failure stops the render and records the verdict in both the attempt
manifest and `module.json`.

## Checks

### Coverage threshold

For Python stacks, `mint` runs the generated unit tests and conformance tests under
a line tracer in separate subprocesses, then merges covered line numbers for files
under the generated module `src/` directory (`.mint/generated/<module>/src/` by
default). For TypeScript stacks, Mint runs Vitest with the v8 coverage provider and
parses `coverage-final.json`.

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

For modules with multiple functional units, coverage and mutation are deferred
until the final unit. Acceptance traceability still runs after every unit. This
keeps an incremental render from failing because a model predeclared later public
functions before their tests exist.

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

For `typescript-lib` and `typescript-node`, missing coverage or mutation tooling is
a hard failure with a fix hint. Generated packages need `typescript`, `vitest`, and
`@vitest/coverage-v8` available to their npm scripts.

## Metadata

Attempt manifest:

```text
.mint/generated/<module>/.mintgen/attempts/<unit>/test-quality-1.json
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
