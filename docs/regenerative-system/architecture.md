# Architecture

## Components

All code lives under `src/mint_cli/`.

| Module | Responsibility |
| --- | --- |
| `cli.py` | argparse front end; maps subcommands to workflow functions |
| `workflow.py` | the orchestrator: planning, the render loop, test gates, checkpoints |
| `specs.py` | parse and validate `*.mint.md` into a `Spec` IR |
| `config.py` | parse `mint.yaml` (a tiny YAML subset) |
| `modgraph.py` | resolve `requires` into a topological render order |
| `hashing.py` | canonical JSON hashing + a content hash of a generated tree |
| `state.py` | `.mintgen/module.json` metadata, render log, per-attempt audit trail |
| `gitutil.py` | nested-repo git operations (init, commit, reset, HEAD) |
| `stacks.py` | target-stack adapters for Python and TypeScript toolchains |
| `test_quality.py` | anti-weak-test gate: coverage, traceability, mutation probe |
| `renderer/` | pluggable renderer adapters + the file-patch contract |
| `errors.py` | `MintError`, the single user-facing error type |

## The render loop (per module)

`render_single_module` runs this pipeline:

1. **Healthcheck** — spec parses, the selected stack adapter can prepare its
   toolchain, `imports`/`requires` resolve, linked resources exist. Fail fast with a
   path + fix hint.
2. **Prepare** — ask the stack adapter to prepare (Python runs the configured
   prepare script; TypeScript checks Node/npm).
3. **Plan** — `determine_render_plan` compares stored hashes against current ones
   and returns a `[start, end]` unit range (or a no-op). See
   [module-graph.md](module-graph.md) and
   [metadata-and-checkpoints.md](metadata-and-checkpoints.md).
4. **Prepare workspace** — for a full render, wipe the output; for a partial render,
   `git reset --hard` the generated repo to the start unit's `beforeCommit` and drop
   the conformance tests for the units about to be re-rendered.
5. **Render each unit** (`render_one_unit`):
   - Build a `RenderRequest` and ask the renderer for a **file patch**.
   - Validate the patch contract before applying it. Malformed JSON, invalid schema,
     or oversized model output is recorded as `patch_invalid` and fed back to the
     model while retries remain.
   - Apply the patch to the generated module dir and the conformance dir.
   - If the spec declares `styleLock`, run the **style-lock gate**: scan the
     generated `src/` for `<style` elements, `style=` attributes, and class
     tokens outside the declared prefix; on failure, re-render with the
     offending lines as feedback (recorded as `style_lock_failed`). Runs before
     the test gates so violations never cost a test run, and again after a
     conformance-phase re-render.
   - Run the stack adapter's **unit gate**; on failure, re-render once with the test
     output as feedback (one retry). Python uses pytest. TypeScript runs
     `npm run typecheck` (`tsc --noEmit`) and Vitest unit tests.
   - Run the stack adapter's **conformance gate** (which also re-runs all prior
     units' conformance tests as regression); on failure, re-render once with
     feedback. TypeScript uses Vitest against `conformance/<module>/`.
   - Run the **test-quality gate**: coverage threshold, acceptance traceability, and a
     lightweight mutation probe. Python uses an in-process pytest tracer and AST
     mutation; TypeScript uses Vitest v8 coverage and TypeScript-compiler-API mutation
     candidate discovery. Each stack-specific check lives behind the stack adapter.
   - Strip runtime caches, update metadata, and commit two checkpoints (code, then
     metadata) to the module's nested git repo.

## Multi-module flow

`render_module(top)` resolves the dependency order with `modgraph.build_render_order`
and runs the per-module loop for each module, **required modules first**. Only the
named (top) module honours `--from` / `--range` / `--force`; required modules use
their default incremental plan, so they are no-ops unless their own spec changed.

Specs may override the configured renderer with `rendererProvider`,
`rendererModel`, and `rendererPromptVersion`. The calc graph uses those fields to
render through replayed model cassettes while the original taskstore/tasklist demo
continues to use deterministic templates.

The `stack` frontmatter selects a target-stack adapter. `python-cli`/`python-lib`
use the Python adapter and the default project scripts. `typescript-lib` and
`typescript-node` use the TypeScript adapter, which drives npm package scripts,
discovers TypeScript source for required-module prompts, and writes explicit local
file dependencies for required TypeScript modules.

The self-hosting proof uses the same override path for `.mint/specs/mint-hashing.mint.md`,
then compares the generated package against the handwritten `mint_cli.hashing`
module in the test suite.

## Data flow

```
.mint/specs/*.mint.md ──parse──▶ Spec IR ──stack adapter + hash──▶ plan ──▶ RenderRequest
                                                                │
                                                                ▼
                                                Renderer adapter (local | model)
                                                                │  file patch (JSON)
                                                                ▼
                                    apply_patch ──▶ .mint/generated/<m>/ + conformance/<m>/
                                                                │
                                   stack unit + conformance + test-quality gates
                                                                │ pass
                                                          ▼
                          .mintgen/module.json + git checkpoints (nested repo)
```

## Why a patch, not direct writes

Every renderer — deterministic or model — emits the **same** structured artifact: a
JSON list of file operations. That makes the two paths interchangeable, gives one
uniform audit trail (prompt, response, parsed patch, stdout/stderr, classification
per attempt), and lets us validate/sandbox writes before they touch disk.
