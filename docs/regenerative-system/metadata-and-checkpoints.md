# Metadata & checkpoint model

Each generated module owns a `.mintgen/` directory inside its nested git repo:

```
generated/<module>/
  .git/                      # nested repo; checkpoints per unit
  .mintgen/
    module.json              # the metadata record (below)
    render.log               # append-only render trace
    reports/
      latest.json            # structured run report
      budget-abort.json      # present after a budget abort
    attempts/<unit>/         # full audit trail per attempt
      unit-1.json            # manifest: classification, exit, paths
      unit-1.prompt.txt      # renderer prompt
      unit-1.response.txt    # raw renderer response
      unit-1.patch.json      # parsed/validated patch
      unit-1.stdout.log      # test stdout
      unit-1.stderr.log      # test stderr
      conformance-1.json …
      test-quality-1.json …  # coverage/traceability/mutation verdict
  src/ tests/ …              # the generated code
```

`.mintgen/` is committed inside the module repo, but generated runtime caches are
kept out of checkpoints and hashes. Python runs disable bytecode writes
(`PYTHONDONTWRITEBYTECODE=1`) and strip `__pycache__`, `.pytest_cache`, and `*.pyc`.
TypeScript runs ignore package-manager/build state such as `node_modules`, `.vite`,
`.vitest`, `coverage`, and `*.tsbuildinfo`.

## `module.json`

```jsonc
{
  "version": 1,
  "module": "tasklist",
  "specPath": ".mint/specs/tasklist.mint.md",
  "renderId": "2026-06-27T…-tasklist",
  "provider": "local",
  "model": "deterministic-python-cli-v0",
  "promptVersion": "v0",

  "specHash": "…",                 // whole spec IR
  "nonFunctionalSpecHash": "…",    // IR minus functional units
  "importedContextHash": "…",      // imported modules' shared context
  "requiredModuleCodeHash": "…",   // required modules' generated code
  "generatedCodeHash": "…",        // this module's own code

  "lastSuccessfulUnitId": "FR2",
  "functionalUnits": [
    {
      "id": "FR1",
      "title": "…",
      "textHash": "…",             // per-unit spec text
      "status": "passed",
      "renderer": "deterministic",
      "beforeCommit": "<sha>",     // checkpoint to roll back to for re-render
      "implementationCommit": "<sha>",
      "unitTestsCommit": "<sha>",
      "conformanceCommit": "<sha>",
      "finishedCommit": "<sha>",
      "attempts": {"implementation": 1, "unit": 1, "conformance": 1, "testQuality": 1},
      "testQuality": {"status": "passed", "...": "..."},
      "startedAt": "…", "finishedAt": "…"
    }
  ]
}
```

## How the plan reads it

`determine_render_plan` returns the first unit that must be re-rendered (everything
from there to the end is rebuilt), or a no-op. In priority order:

1. explicit `--range` / `--from` / `--force`
2. no metadata → full render
3. `nonFunctionalSpecHash` changed → full render
4. `importedContextHash` changed → full render
5. `requiredModuleCodeHash` changed → full render
6. first unit whose `textHash` changed, is missing, or didn't pass → from there
7. unit set size mismatch → full render
8. otherwise → **no-op**

This is what makes re-renders minimal: editing one later unit rebuilds only that
unit onward; everything before its `beforeCommit` is preserved exactly.

## Checkpoints

For each unit, after unit, conformance, and test-quality gates pass, the workflow
commits twice to the nested repo: a **code** commit (`[mint] completed FRn: …`) and a **metadata** commit
(`[mint] metadata FRn: …`). Commit bodies record module, unit, render id, provider,
prompt version, and model. `beforeCommit` (the HEAD before the unit) is the rollback
point a partial re-render resets to.

If a render aborts after one or more units pass but before the next unit records its
own `beforeCommit`, the next render resumes from the previous passed unit's
`finishedCommit`. That preserves completed units and starts at the first missing or
failed unit instead of forcing a full render.

## Attempts & retries

Each render/test attempt writes a full manifest under `attempts/<unit>/`. The unit
phase allows one retry (`limits.unitRetries`); the conformance phase allows one retry
(`limits.conformanceRetries`) that re-renders with the failing output as feedback and
re-checks the unit tests for regression. `classification` is one of `rendered`,
`passed`, `patch_invalid`, `<phase>_failed`, or `no_tests` — a zero-test gate is
treated as a failure, never a pass. The test-quality phase writes
`test-quality-1.json` with classification `passed` or `test_quality_failed`.
Budget aborts write `.mintgen/reports/budget-abort.json` with the limit, usage, and
unit/phase where the abort happened.
