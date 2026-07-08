# Spec format (`*.mint.md`)

A spec is Markdown with a YAML frontmatter block. One spec describes one module.
The file must be named `<module>.mint.md` and live under the configured
`specsDir`, which defaults to `.mint/specs/`.

## Example

```markdown
---
module: tasklist
description: Task-list CLI built on the shared taskstore library
imports: [taskstore]
requires: [taskstore]
stack: python-cli
template: tasklist
---

## definitions

- Command: a subcommand of the `tasklist-cli` console program.

## implementation

- Use Python 3.12.
- Provide a console command named `tasklist-cli`.
- Persist tasks via the required `taskstore` library, not a private store.

## test

- Conformance tests use pytest.
- Conformance tests drive the CLI as a subprocess, never private helpers.

## functional

- id: FR1
  title: Add prints the new task as incomplete
  spec:
    - `tasklist-cli add "Buy milk"` stores the task via taskstore.
    - The command prints the task with an incomplete marker `[ ]`.
  acceptance:
    - After `tasklist-cli add "Buy milk"`, `tasklist-cli list` shows `[ ] Buy milk`.
```

## Frontmatter keys

| Key | Required | Meaning |
| --- | --- | --- |
| `module` | yes | Module name; must equal the filename stem |
| `description` | no | Free text |
| `stack` | yes | Target stack (`python-cli`, `python-lib`, `typescript-lib`, `typescript-node`) |
| `imports` | no (`[]`) | Modules whose shared context is pulled in (see [module-graph.md](module-graph.md)) |
| `requires` | no (`[]`) | Modules that must be built first |
| `template` | no | Deterministic template to use; defaults to `module` |
| `rendererProvider` | no | Per-spec renderer override, e.g. `model`, `anthropic`, `claude-cli`, or `codex-cli` for replayed template-free specs |
| `rendererModel` | no | Per-spec model id override used by cassettes |
| `rendererPromptVersion` | no | Per-spec prompt version override used by cassettes |

Inline lists (`[a, b]`) and `[]` are supported by the YAML subset in `config.py`.

Template-free model specs omit `template` and set the renderer override keys. The
deterministic renderer remains for the built-in demo templates and tests.

## TypeScript example

TypeScript stacks are model/replay only. Like every spec, a `typescript-lib`
spec must include all four `##` body sections (`definitions`, `implementation`,
`test`, `functional`); omitting any of them fails parsing with
`Spec has no <name> section`. A complete minimal `typescript-lib` spec:

```markdown
---
module: calc-ts
description: TypeScript calculator library
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: your-model-id
rendererPromptVersion: calc-ts-v1
---

## definitions

- Calculator: the public API exported from `src/index.ts`.

## implementation

- Expose the public API from `src/index.ts`.
- `package.json` scripts run `tsc --noEmit`, `vitest run tests`, and `vitest run`.

## test

- Tests use vitest and import only the public API from `src/index.ts`.

## functional

- id: FR1
  title: add returns the sum of two numbers
  spec:
    - Export an `add(a, b)` function.
  acceptance:
    - `add(2, 3)` returns `5`.
```

See [typescript.md](typescript.md) for the generated package contract and current
limits.

## Body sections

Four `##` sections, all required and non-empty:

- **`## definitions`** — `- Name: text` lines (the shared vocabulary).
- **`## implementation`** — `- bullet` non-functional implementation requirements.
- **`## test`** — `- bullet` testing requirements.
- **`## functional`** — the functional units (below).

The first three are the **non-functional** part of the spec. A change to any of them
forces a full re-render (their combined hash is `nonFunctionalSpecHash`).

## Functional units

Each unit is a YAML-ish block:

```
- id: FR1
  title: <one line>
  spec:
    - <bullet> …
  acceptance:
    - <bullet> …
  resources:        # optional
    - path/to/file
```

Rules enforced by `specs.py` (each raises a `MintError` naming the file):

- `id` matches `FR<number>` (`FR1`, `FR2`, …).
- ids are **unique** and in **strictly ascending** numeric order.
- `title`, `spec`, and `acceptance` are present and non-empty.
- listed `resources` must exist (checked at healthcheck).
- a module may not `import` or `require` itself.

## Hashes computed from a spec

| Hash | Over | Drives |
| --- | --- | --- |
| `specHash` | whole IR | informational / drift detection |
| `nonFunctionalSpecHash` | IR minus functional units | full re-render trigger |
| per-unit `textHash` | one unit's id/title/spec/acceptance/resources | per-unit re-render trigger |
| `importedContextHash` | imported modules' shared context | full re-render trigger |
| `requiredModuleCodeHash` | required modules' public interface (the prompt context payload) | full re-render trigger |

See [metadata-and-checkpoints.md](metadata-and-checkpoints.md) for how they're stored
and compared.
