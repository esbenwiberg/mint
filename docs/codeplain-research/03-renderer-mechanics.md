# Renderer Mechanics

This file summarizes what the public `Codeplain-ai/codeplain` client reveals.
It does not include the proprietary hosted render implementation.

## CLI Entry Point

The public CLI lives around:

- `plain2code.py`
- `plain2code_arguments.py`
- `module_renderer.py`
- `partial_rendering.py`
- `plain_modules.py`
- `plain_file.py`
- `plain_spec.py`
- `render_machine/*`

The CLI parses args, resolves config, parses `.plain`, creates a `PlainModule`,
then hands it to `ModuleRenderer`.

Important CLI options:

- `--dry-run`
- `--full-plain`
- `--render-from <frid>`
- `--render-range <start,end>`
- `--force-render`
- `--headless`
- `--unittests-script`
- `--conformance-tests-script`
- `--prepare-environment-script`
- `--template-dir`
- `--copy-build`
- `--build-dest`

## Module Metadata

The client writes metadata under generated module folders:

```text
plain_modules/<module>/.codeplain/module_metadata.json
```

The metadata includes:

- `source_hash`
- `non_functional_source_hash`
- `required_modules_code_hash`
- `functionalities`
- `required_modules_functionalities`

This metadata is what lets the client decide whether a module's spec changed,
whether upstream generated code changed, and which functional specs existed at
the last successful render.

## Change Detection

`partial_rendering.py` and `change_detection.py` decide whether a render can
resume or must restart.

Key logic:

- If a full source hash changed, a module may need rendering.
- If non-functional content changed, partial render is unsafe.
- Non-functional content includes definitions, implementation reqs, test reqs,
  and linked resources.
- If only functional specs changed, the client compares old and new functional
  requirement lists.
- It classifies changes as added, removed, edited, or moved.
- It starts from the earliest affected FRID.

For code changes in required modules, the client checks the generated-code hash
of the last required module. If that code changed, downstream modules are
affected.

## Module Rendering

`module_renderer.py` recursively renders required modules first unless a render
choice targets a specific module.

It skips rendering when all of these are true:

- force render is off
- the module has already been loaded in this run
- a generated repo exists
- required module code did not change
- the Plain spec did not change
- no targeted render choice applies

If a prior module changes, later modules can be wiped.

## Git Checkpoints

Generated module folders are Git repositories. The client uses commits as
checkpoints.

Important commit messages include:

- initial module commit
- base folder copied
- implemented code and unit tests for functionality
- refactored code after implementing functionality
- fixed issues found during conformance testing
- functionality ID fully implemented

Commit messages include FRID, module name, and render ID. This lets the client:

- find the last rendered functionality
- reset to the commit before a render range
- diff implementation output for a FRID
- compare pre-conformance and post-conformance fixes
- resume from a known point

## Render State Machine

`render_machine/state_machine_config.py` defines the render flow.

For one FRID, the loop is roughly:

1. Prepare repositories.
2. Render the functional requirement.
3. Run unit tests.
4. Fix unit tests if needed.
5. Commit implementation.
6. Refactor code.
7. Run unit tests again.
8. Commit refactor.
9. Generate conformance tests.
10. Prepare testing environment.
11. Run conformance tests.
12. Fix conformance-test failures.
13. Potentially update implementation code.
14. Re-run unit tests if implementation changed.
15. Summarize conformance tests.
16. Commit conformance-test changes.
17. Analyze specification ambiguity.
18. Mark FRID fully implemented.
19. Move to the next FRID.

This is why "regenerate code" is not a single model call. It is a controlled
loop around model calls, shell scripts, local files, Git state, and retry limits.

## Hosted API Surface

The public client calls API endpoints such as:

- `/render_functional_requirement`
- `/fix_unittests_issue`
- `/render_conformance_tests`
- `/render_acceptance_tests`
- `/fix_conformance_tests_issue`
- `/analyze_rendering`
- `/finish_functional_requirement`
- `/fail_functional_requirement`
- `/summarize_finished_conformance_tests`

The public client sends:

- FRID
- parsed plain source tree
- linked resources
- existing generated files
- memory files
- module name
- required-module functionality summaries
- test output or issue text
- code diffs

This reveals the pattern even though the server prompts and models are not
public.

## Conformance Loop

The conformance loop is the safety gate. It can decide the issue is:

- the conformance test
- the implementation code
- conflicting requirements
- conflicting acceptance tests

If implementation changes during conformance fixes, the client can re-run unit
tests and then regression conformance tests for previous FRIDs.

Retry limits exist, but plain-forge's `run-codeplain` skill adds a human/agent
supervisor that should stop earlier when a loop is clearly not converging.

