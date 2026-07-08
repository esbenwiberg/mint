# Known limits and next risks

What this system deliberately does *not* do yet, and where it would bite first.

## Limits

- **Deterministic renderer is template-bound.** It renders only modules it has a
  template for (`renderer/templates/`), keyed by the spec `template` (or module
  name). It is "deterministic = repeatable for offline tests," not "general." Truly
  free-form specs are the model renderer's job.
- **Live recording remains manual.** The model path has record/replay cassettes,
  the calc graph proves offline replay for a non-trivial template-free project, and
  `mint-hashing` proves a small self-hosted mint component, but CI still should not
  call the live provider. Real model nondeterminism remains isolated behind
  `MINT_LIVE=1`.
- **One retry per phase.** Unit and conformance each get a single feedback-driven
  retry. Deeper failures fail the render; there is no escalation or human-in-the-loop.
- **Required-module change re-renders the whole dependent.** A code-dependency change
  rebuilds the dependent from FR1 rather than computing the minimal affected unit.
  Safe, but not minimal.
- **Hash-based change detection only.** Re-render is driven by content hashes, not by
  understanding *what* changed. A whitespace-only spec edit re-renders; a semantically
  irrelevant dependency change still cascades.
- **TypeScript support is library/Node-only.** `typescript-lib` and
  `typescript-node` use npm-compatible scripts, `tsc --noEmit`, and Vitest, but
  there are no browser UI, React/Vue/Svelte, or bundler-specific stacks yet.
- **TypeScript test-quality is enforced, but only over block-bodied functions.**
  Coverage (Vitest v8), acceptance traceability, and the mutation probe all run for
  `typescript-lib`/`typescript-node`. The mutation probe mutates exported
  function/method bodies discovered through the TypeScript compiler API; concise
  arrow bodies (`=> expr`) are not yet mutated.
- **Mint does not install npm dependencies.** Generated TypeScript packages declare
  scripts and dependencies; the environment must have/install the package tooling
  before those scripts can pass. The test-quality gate additionally needs
  `@vitest/coverage-v8` (coverage) and `typescript` (mutation candidate discovery)
  installed — when either is missing the gate hard-fails with a fix hint rather than
  silently skipping. Override mutation discovery with `MINT_TS_MUTATION_FINDER_COMMAND`
  (reads `MINT_TS_SRC`, writes candidate spans as JSON to stdout).
- **YAML subset.** `config.py` parses a small YAML subset (scalars, nested maps,
  inline `[a, b]` lists). Block-list syntax and anchors are unsupported.
- **Conformance regression is "run everything."** Prior units' conformance tests run
  again through the selected stack adapter (pytest for Python, Vitest for
  TypeScript) — correct, but it scales linearly and has no selective regression.
- **Run-report costs are estimates.** Reports estimate tokens from prompt/response
  text length and set cost to zero until provider pricing is configured.

## Next risks (in rough priority)

1. **Model output trust.** Patch schema failures and oversized responses now feed
   back through retries, and the test-quality gate catches shallow tests. A model can
   still write plausible-wrong code that passes a stronger-but-incomplete suite.
2. **Cache/staleness traps.** We already disable bytecode writes because same-second,
   same-size regeneration served stale `.pyc` (a real bug found in development). Other
   stateful caches (import caches in long-lived processes, build artifacts, lockfiles)
   could reintroduce the class. Keep generated test runs hermetic.
3. **Cross-module version skew.** `requiredModuleCodeHash` detects that a dependency's
   public interface changed, but there is no API-compatibility check. A required module
   can change its public API and the dependent will re-render against it — fine if the
   model/template adapts, silent breakage if it doesn't until the gate catches it. The
   inverse gap is deliberate: an upstream *behavior* change behind a stable interface
   does not re-run dependents' gates; use `mint render <dependent> --force` when
   upstream semantics changed without a surface change.
4. **Partial-render correctness under reordering.** Inserting a unit in the middle or
   renumbering shifts the plan boundary; the ascending-order rule helps, but heavy
   spec refactors are best handled with `--force`.
5. **Concurrency.** Rendering assumes a single writer per module. Parallel renders of
   the same graph would race on the nested repos and metadata.
