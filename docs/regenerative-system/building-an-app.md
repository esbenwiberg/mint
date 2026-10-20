# Building an app from specs

An app in Mint is a module graph whose leaves you can run. There is no separate
"app spec": you write one `*.mint.md` per bounded module, wire them with
`requires`, render the graph, and `mint run` composes the generated sources into
a process. The worked example is [`examples/timesheet`](../../examples/timesheet):
a store, a rules engine, an HTTP API, a CLI, a design system, and a web UI.

## 1. Carve the app into modules

Pick boundaries a test can grip and a dependent can import:

| Layer | Example module | Stack | Why it is its own module |
| --- | --- | --- | --- |
| storage | `timestore` | `python-lib` | one data contract, no business rules |
| rules | `rules` | `python-lib` | pure functions, trivially testable, reused by API and CLI |
| HTTP API | `timesheet-api` | `python-lib` | an app factory; declares its third-party deps |
| command line | `timesheet-cli` | `python-cli` | the runnable entrypoint |
| design system | `ui-kit` | `python-lib` | the look, pinned as literal constants |
| web UI | `timesheet-web` | `python-lib` | an app factory under a style lock |

Each spec declares what it builds on:

```yaml
---
module: timesheet-cli
requires: [timestore, rules]     # rendered first; their public stubs are in the prompt
imports: [timestore, rules]      # their definitions are shared context
stack: python-cli
entrypoint: timesheet_cli        # what `mint run timesheet-cli` executes
---
```

`requires` is the code dependency (ordering + PYTHONPATH); `imports` is shared
vocabulary only. See [module-graph.md](module-graph.md). The spec body pins the
public API the dependents and the entrypoint rely on: a package name, an app
factory signature, exit codes. Those bullets are what the conformance tests and
`mint run` hold the renderer to; see [`../spec-authoring.md`](../spec-authoring.md).

## 2. Render the graph

```bash
mint lint timesheet-cli            # before spending a model call
mint render timesheet-cli          # renders timestore, rules, then the CLI
mint render --all                  # or: every spec in the project, dependency order
```

Rendering is incremental: a second `mint render --all` is a NOOP per module, and
editing one unit re-renders only that slice. Cassettes make it offline once
recorded; see [record-replay.md](record-replay.md).

## 3. Run it

```bash
mint run timesheet-cli -- add Apollo 2026-07-06 2.5
mint run timesheet-cli -- list --week 2026-W28
```

`mint run <module>` puts the module's generated `src/`, every required module's
`src/` (dependency order), and every module-local `.mint-deps` on `PYTHONPATH`,
then runs `python -m <entrypoint>`. The environment is computed from the same graph
resolution the conformance gate uses, so what runs is what was tested. Arguments
after `--` go to the app verbatim; without `--`, plain words are fine but flags
would be read by `mint`.

The entrypoint is, in order: `--entry DOTTED`, the spec's `entrypoint:` key, or
`<module>` with dashes as underscores when that package has a `__main__.py`.
`entrypoint:` is runtime wiring, not part of the render contract: adding it never
changes a hash or invalidates a cassette. When nothing resolves, the error lists
the runnable candidates it found under `src/`.

An app factory (API, web UI) has no `__main__`; run the server of your choice
inside the environment instead:

```bash
python3.12 -m pip install uvicorn      # dev tool, not a module dependency
mint exec timesheet-web -- python -c "
import uvicorn
from timesheet_web import create_web_app
uvicorn.run(create_web_app('/tmp/ts-web-store.json'), port=8000)
"
```

`mint exec <module> -- <command…>` runs any command with the same environment and
exit code. To wire the environment into something else (an IDE, a Docker
`ENV`, a `.env` file), print it:

```bash
mint env timesheet-web                 # export lines, eval-able
mint env timesheet-web --format path   # bare PYTHONPATH
mint env timesheet-web --format json   # modules, paths, stale list
eval "$(mint env timesheet-web)"
```

## What can go wrong

- **Unrendered graph.** `env`/`run`/`exec` refuse with the modules that lack
  generated output and the `mint render` command that fixes it.
- **Stale generated code.** When a spec or dependency has moved since the last
  render, the command still runs (you may be mid-edit) but prints a warning on
  stderr naming the stale modules; `mint env` lists them in a comment / the
  `stale` JSON field. Re-render before trusting behaviour.
- **Bytecode caches.** The environment sets `PYTHONDONTWRITEBYTECODE=1`: a
  regenerated file can land in the same second with the same size as the old
  one, and a stale `.pyc` would shadow it.
- **Interpreter mismatch.** `.mint-deps` were installed by the interpreter that
  rendered the module (`PYTHON_BIN`, defaulting to the one running `mint`).
  `mint run` uses the same rule; `mint exec` runs whatever command you give it,
  so pass the matching `python`.
- **Not Python.** TypeScript modules render and test, but graph composition for
  running them is not built yet; the commands refuse and say so. See
  [known-limits.md](known-limits.md).
- **No hot reload, no asset pipeline.** Mint produces source trees, not a dev
  server. Static assets are transcribed by the renderer, not copied by Mint.
