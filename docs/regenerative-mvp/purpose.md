# Purpose

## Goal

Build a local regenerative coding workflow that proves this claim:

> If the spec is the maintained artifact, generated code can be deleted and
> recreated from a small, test-gated render unit without losing intent.

The MVP should not clone Codeplain. It should copy the load-bearing practices
identified in the research pack:

- specs as source
- generated code as output
- small functional units
- test gates
- metadata and Git checkpoints
- supervised retry loops

See [../codeplain-research/05-build-our-own.md](../codeplain-research/05-build-our-own.md)
for the original local-build sketch.

## What This MVP Proves

The MVP proves five things:

1. A human-readable spec can be parsed into deterministic render units.
2. A renderer can regenerate one unit at a time.
3. Generated output can be checkpointed and rolled back without touching the
   main repo history.
4. Unit and conformance scripts can gate acceptance of generated output.
5. Agent workflows can fix the spec when behavior is wrong, instead of editing
   generated code.

## What This MVP Does Not Prove

The MVP does not try to prove:

- full Plain language compatibility
- multi-module dependency graph rendering
- production-grade model routing
- arbitrary language/framework support
- fully autonomous repair
- strong guarantees for safety-critical software
- long-term generated-code quality across large systems

Those are later questions. The first loop only has to be small, inspectable, and
repeatable.

## Target First Use Case

The first test case is a small Python CLI/library module with clear observable
behavior. Good examples:

- a task-list CLI with a small public library API
- a Python CLI that transforms files
- a schema-driven data normalizer

Avoid subjective UI polish, server lifecycle complexity, and large tightly
coupled apps for the first run. The research notes that this pattern fits
integrations, SDKs, schema-heavy backends, CLIs, and internal tools better than
highly visual or tightly coupled systems.

## Success Criteria

The MVP is successful when we can:

- write one `specs/<module>.mint.md`
- run `mint healthcheck <module>`
- run `mint render <module>`
- inspect generated code under `generated/<module>/`
- see unit and conformance tests pass
- edit one functional unit in the spec
- run `mint render <module> --from <unit-id>`
- see the renderer roll back to the prior checkpoint and regenerate only the
  affected unit and later units

## Core Constraint

Generated code is read-only for humans and agents. It can be inspected as
evidence, but durable fixes go into:

- specs
- linked resources
- config
- test scripts
- renderer prompts/tools

Never rely on hand edits under `generated/<module>/` as the fix.
