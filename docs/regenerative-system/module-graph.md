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

### The required-interface cascade

After rendering, a module records `requiredModuleCodeHash` — a hash over the
**public interface** of every module it (transitively) requires, computed from the
same context payload the dependent's render prompt embeds
(`adapter.required_context_files`). For Python that payload is AST-derived
interface stubs: signatures, docstrings, and public constants, with bodies
elided. For TypeScript it is currently the full source.

On the next render, if a required module's visible interface changed, the hash no
longer matches and the dependent re-renders **from FR1** (the whole module),
because a dependency change can affect any unit. Reason string:
`required module code changed`.

Because prompt context and cascade hash are the same object, the cascade fires
exactly when what the dependent can see changes — and **not** otherwise:

- Internal-only upstream changes (bodies, comments, private helpers, the private
  `_mint_provenance.py` file) leave dependents untouched: no re-render, and their
  replay cassettes stay valid. This is what keeps re-record cost from growing
  with graph depth.
- Interface-visible changes (signatures, docstrings, public constants, public
  files added/removed) cascade as before.

Worked example (calc graph):

```
edit .mint/specs/lexer.mint.md so the regenerated tokenize() docstring changes
mint render calc-cli
  → RENDER lexer       (Reason: functional unit changed: FR1)    # required first
  → RENDER parser      (Reason: required module code changed)    # interface moved
  → RENDER evaluator   (Reason: required module code changed)
  → RENDER calc-cli    (Reason: required module code changed)

edit .mint/specs/lexer.mint.md so only a body comment changes
mint render calc-cli
  → RENDER lexer       (Reason: functional unit changed: FR1)
  → NOOP parser                                                  # same interface
  → NOOP evaluator
  → NOOP calc-cli
```

The trade-off is deliberate: an upstream *behavior* change behind a stable
interface does not re-verify dependents automatically. Re-run a dependent's
gates explicitly (`mint render <dependent> --force`) when upstream semantics —
not surface — changed.

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
