# Module graph behavior

Two frontmatter keys connect modules. They are deliberately distinct.

## `requires` — build ordering + code dependency

`requires: [taskstore]` means "build `taskstore` before me, and my generated code
depends on its generated code." `modgraph.build_render_order` walks `requires`
transitively from the named module and returns a topological order with required
modules first and the named module last. It detects:

- **cycles** — `MintError` naming the cycle path.
- **missing specs** — `MintError` naming the chain that required the missing module.

`mint render <top>` renders every module in that order. Required modules use their
default incremental plan, so they are no-ops unless their own spec changed.

### The required-code cascade

After rendering, a module records `requiredModuleCodeHash` — a hash over the
generated code of every module it (transitively) requires. On the next render, if a
required module's code changed, that hash no longer matches and the dependent
re-renders **from FR1** (the whole module), because a dependency change can affect
any unit. Reason string: `required module code changed`.

This is why generated code carries provenance (`_mint_provenance.py`): editing a
required module's spec changes its generated code, which moves the hash, which
cascades. Worked example:

```
edit .mint/specs/taskstore.mint.md (FR2 text)
mint render tasklist
  → RENDER taskstore   (Reason: functional unit changed: FR2)   # required first
  → RENDER tasklist    (Reason: required module code changed)    # dependent cascades
```

At test time, dependents must import their required modules. The selected stack
adapter owns that wiring. Python passes required modules' `src/` dirs to the test
scripts via `MINT_REQUIRED_SRC`, and the scripts prepend them to `PYTHONPATH`.
TypeScript writes explicit `file:../required-module` dependencies into the
dependent `package.json` before running npm scripts. The transitive closure is
included, so `A → B → C` works.

## `imports` — shared context (no code dependency)

`imports: [taskstore]` pulls `taskstore`'s **definitions, implementation, and test
requirements** into the render context as shared vocabulary, without making your
generated code depend on its generated code. The combined imported context is hashed
as `importedContextHash`; a change forces a full re-render. Reason string:
`imported context changed`.

A module commonly both `requires` and `imports` the same dependency (build-order +
runtime code, plus shared definitions). That overlap is allowed.

## Each module is its own repo

Every generated module is an independent nested git repository under
the configured `generatedDir` (`.mint/generated/<module>/` by default), with its
own history and its own `.mintgen/module.json`. The outer repo `.gitignore`s the
default generated output and `conformance/*`. Re-rendering a slice uses that
nested history to roll back precisely (`git reset --hard <beforeCommit>`).
