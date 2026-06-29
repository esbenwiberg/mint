# TypeScript stacks

Mint supports generated TypeScript modules through the stack adapter boundary.
The first supported stacks are:

- `typescript-lib` — a Node/npm TypeScript library package.
- `typescript-node` — same tooling, with prompt guidance to expose a `bin` CLI entry.

Both stacks render only through the model/replay path. There are no deterministic
TypeScript templates yet.

## Create a TypeScript spec

```bash
CODEX_MODEL=your-codex-model-id
mint new calc-ts \
  --stack typescript-lib \
  --renderer codex-cli \
  --model "$CODEX_MODEL" \
  --prompt-version calc-ts-v1

$EDITOR .mint/specs/calc-ts.mint.md
mint lint calc-ts
mint healthcheck calc-ts
MINT_LIVE=1 mint live-smoke calc-ts
mint render calc-ts
mint report calc-ts
```

Offline renders replay local cassettes after the first live recording. Missing or
stale replay cassettes fail with the same `MINT_LIVE=1 mint live-smoke <module>`
fix hint used by Python model specs.

## Generated package contract

The renderer prompt tells the model to keep all generated files inside the
configured generated module root (`.mint/generated/<module>/` by default) and
conformance tests inside `conformance/<module>/`.

For `typescript-lib`, model patches should write:

```text
.mint/generated/<module>/package.json
.mint/generated/<module>/tsconfig.json
.mint/generated/<module>/src/**/*.ts
.mint/generated/<module>/tests/**/*.test.ts
conformance/<module>/FRn/**/*.test.ts
```

`package.json` must include npm-compatible scripts:

```json
{
  "scripts": {
    "typecheck": "tsc --noEmit",
    "test:unit": "vitest run tests",
    "test:conformance": "vitest run"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "vitest": "^3.0.0"
  }
}
```

The TypeScript adapter runs `npm run typecheck`, then `npm run test:unit`, then
`npm run test:conformance -- <absolute conformance dir>`. Projects may use npm,
pnpm, or another package manager to install dependencies, but the generated package
must expose npm-compatible scripts.

## Required modules

When a TypeScript module declares `requires: [other-module]`, Mint renders required
modules first. Before running tests for the dependent module, the TypeScript adapter
updates the dependent `package.json` with explicit local file dependencies:

```json
{
  "dependencies": {
    "other-module": "file:../other-module"
  }
}
```

Prompts include required modules' `package.json`, `tsconfig.json`, and `src/**/*.ts`
contents with TypeScript code fences, so the model can import public APIs by package
name. `requiredModuleCodeHash` remains stack-neutral; edits to a required module's
generated code still force a full dependent re-render.

## Current limits

- TypeScript test-quality is recorded as `skipped`: coverage, acceptance
  traceability, and mutation probes are Python-only for now.
- Mint does not run `npm install` for you. Generated packages must have dependencies
  installed in the environment where their npm scripts run, or the scripts will fail
  with their normal npm/tooling errors.
- No browser UI, React/Vue/Svelte, or bundler-specific app generation is supported
  yet.
- No deterministic TypeScript templates are included.
