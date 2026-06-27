# What Codeplain Is

## Product Positioning

Codeplain presents itself as an AI-powered code generation platform that turns
structured specifications written in Plain into production-ready software.

The product pitch has three connected claims:

- Specs are the source of truth.
- Generated code should be reviewed less and regenerated more.
- Unit and conformance tests make generated code safe enough to accept as output.

The product site describes Plain as a structured English specification language
for AI code generation. The site also says Codeplain auto-generates unit and
conformance tests and executes them in the user's infrastructure.

The pricing page gives an important implementation clue: the billing unit is a
successfully rendered functionality. That aligns with the public client code,
where each functional requirement receives a functionality ID and is rendered
incrementally.

## Publicly Visible Split

There are two public repositories with different roles:

- `Codeplain-ai/plain-forge`
  - An npm package.
  - Installs skills, rules, and docs into agent directories such as `.codex/`,
    `.claude/`, `.forgecode/`, or `.agents/`.
  - Helps an agent author and maintain `.plain` specs.
  - Does not render code.

- `Codeplain-ai/codeplain`
  - A Python CLI/client.
  - Parses Plain files.
  - Calls the hosted Codeplain API.
  - Writes generated code into `plain_modules/`.
  - Writes conformance tests into `conformance_tests/`.
  - Runs local test scripts.
  - Tracks render progress, metadata, and Git checkpoints.

The hosted render API is the proprietary center. The local workflow around it is
visible enough to reproduce as a pattern.

## Article Context

The New Stack article frames Codeplain as part of a broader "regenerative
software" or "phoenix architecture" idea: preserve intent and provenance, treat
generated implementation as replaceable.

Important facts from the article:

- Codeplain was founded in early 2025 and launched quietly in September 2025.
- It uses Plain as the specification source of truth.
- It is rolling out `plain-forge` as an open-source agent skills framework.
- It claims spec-generating agents use fewer tokens than direct code generation.
- It uses faster, cheaper models for code rendering, while stronger agents can
  focus on research and spec writing.
- Incode is cited as a production customer using Codeplain for external data
  provider integrations.

## Honest Interpretation

Codeplain is not "press a button and magic code appears". It is closer to a
compiler plus a test-driven agent harness:

- Plain is the source language.
- The renderer is the compiler-like translator.
- Local test scripts are the runtime verification gates.
- Git checkpoints provide incremental build state.
- Agent skills maintain the source language.

That means the interesting part is not just LLM output quality. The interesting
part is the surrounding machinery that decides what context to send, when to
retry, when to rollback, and where regeneration should start.

