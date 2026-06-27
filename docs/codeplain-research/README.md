# Codeplain Research Pack

Research snapshot: 2026-06-27.

This folder captures the useful context from the Codeplain / Plain / plain-forge
research thread so future work can start from durable notes instead of chat
memory.

## Reading Order

1. [Capsule](./capsule.md) - shortest reloadable summary.
2. [What Codeplain Is](./01-what-codeplain-is.md) - product model and claims.
3. [Plain Language And Slicing](./02-plain-language-and-slicing.md) - how specs
   create rewrite boundaries.
4. [Renderer Mechanics](./03-renderer-mechanics.md) - what the public client code
   reveals about the render loop.
5. [Plain Forge Practice](./04-plain-forge-practice.md) - how their agent skills
   keep specs healthy.
6. [Build Our Own](./05-build-our-own.md) - a practical local architecture for a
   Codeplain-like workflow.
7. [Risks And Questions](./06-risks-and-open-questions.md) - limits, failure
   modes, and things to validate.
8. [Sources](./sources.md) - URLs and specific source files inspected.

## Core Takeaway

Codeplain is best understood as a spec-first regenerative build system:

- `.plain` files are the maintained source of truth.
- Generated code under `plain_modules/` is disposable output.
- A module graph and per-functionality IDs define where regeneration starts.
- Generated unit and conformance tests act as the acceptance gate.
- Git checkpoints inside generated module folders make rollback, resume, and
  partial rendering mechanical.

The proprietary piece is the hosted render API. The surrounding practice is much
less mysterious: structured specs, authoring discipline, a dependency graph,
test gates, retry loops, metadata hashes, and a render supervisor.

