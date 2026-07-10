# timesheet — session-metered personal timesheet: backend, CLI, and web UI

A small single-user timesheet built as a Mint spec graph, plus hand-written
Claude Code glue that meters coding sessions automatically: a SessionStart hook
starts a timer, a SessionEnd hook stops it and books the elapsed time as a
draft entry. It is a personal app — there is no user identity anywhere, just
projects, dates, and hours.

```bash
timesheet add Apollo 2026-07-06 2.5   # book time by hand
timesheet start Apollo                # ... or run a timer
timesheet stop                        # -> #2 Apollo 2026-07-08 1.5h draft
timesheet submit 2                    # draft -> submitted
```

## Module graph

`requires` edges, rendered bottom-up:

```text
timestore ─┬─> timesheet-api
rules ─────┼─> timesheet-cli
           └─> timesheet-web <─── ui-kit
```

- **timestore** — JSON-file-backed store of entries
  `{id, project, date, hours, status}`; CRUD plus queries by project, by date,
  and by ISO week (`2026-W28`); store path from `TIMESHEET_STORE`; typed
  `StoreError`. Stdlib only.
- **rules** — pure functions, no I/O: `validate_hours` (positive, and a day's
  total capped at exactly 24), the approval state machine
  `draft -> submitted -> approved | rejected`, and `assert_editable` (approved
  entries are immutable); typed `RuleError`.
- **timesheet-api** — FastAPI app factory `create_app(store_path)` composing
  both. Declares `fastapi`/`httpx` in its generated `pyproject.toml`; Mint
  installs them into a module-local `.mint-deps` before tests.
- **timesheet-cli** — the `timesheet` command: `add`, `list`, `start`, `stop`,
  `status`, `submit`. Timer file path from `TIMESHEET_TIMER`; `stop` rounds to
  the nearest 0.1h (minimum 0.1) and books the entry via timestore.
- **ui-kit** — the design system as data: `TOKENS_CSS` (a string constant
  pinning every color, spacing, shadow, and type token in its spec's acceptance
  bullets) and the `page(title, body)` HTML shell. Stdlib only.
- **timesheet-web** — FastAPI app factory `create_web_app(store_path)` serving
  a server-rendered HTML UI: a filter toolbar (week/project), an entries card
  with status pills and a running hours total, an add form, and per-row actions
  that only offer the legal transitions (draft → Submit; submitted →
  Approve/Reject; terminal statuses → none).

### How the web UI keeps the same look across renders

Two mechanisms, both mint-native:

1. **Replay determinism** — committed cassettes make `mint render` byte-stable;
   the UI only changes when a spec changes and someone re-records live.
2. **The style lock** — the look lives in ui-kit, not in timesheet-web. The
   Python interface stub keeps public *literal* constants verbatim, so
   dependents see the full `TOKENS_CSS` in their render prompt and it is hashed
   into `requiredModuleCodeHash`. The timesheet-web spec then forbids freelance
   styling — no `<style>` of its own, no `style=` attributes, only `ts-*`
   classes — and its conformance tests enforce that mechanically. A live
   re-render may reshuffle markup, but the skin cannot drift; restyling the app
   means editing ui-kit's spec, which cascades a re-render of its dependents.

Semantics conformance can grip (routes, `data-testid` hooks, escaped output,
status codes) are pinned in acceptance bullets; pixels are never asserted —
screenshot tests would fail every legitimate re-render by design.

### CLI exit codes

| Condition | Exit code |
|-----------|-----------|
| success | `0` |
| usage / validation error (`RuleError`, bad date or week, missing env var) | `2` |
| unknown entry id (`StoreError`) | `3` |
| timer already running / no running timer | `4` |

### API error contract

| Condition | Status |
|-----------|--------|
| entry created | `201` |
| query / transition / edit ok | `200` |
| invalid body, bad date or week, rule violation | `422` |
| unknown entry id or project | `404` |
| illegal transition, edit after approval | `409` |

A project is unknown (`404`) when the store holds no entries for it at all; a
known project with no entries in the requested week is `200 []`.

## Render this graph

Cassettes are recorded and committed under `resources/cassettes/`, so rendering
replays offline — no provider, no network:

```bash
cd examples/timesheet
mint render timesheet-api    # timestore, rules, timesheet-api
mint render timesheet-cli    # NOOPs the shared deps, renders the CLI
mint render timesheet-web    # NOOPs again, renders ui-kit + the web UI
mint report timesheet-web
```

Serve the web UI in a real browser (dev only — conformance never starts a
server; `uvicorn` is your dev tool, not a module dependency):

```bash
python3.12 -m pip install uvicorn
G=$PWD/.mint/generated
PYTHONPATH="$G/timesheet-web/src:$G/timestore/src:$G/rules/src:$G/ui-kit/src:$G/timesheet-web/.mint-deps" \
  python3.12 -c "
import uvicorn
from timesheet_web import create_web_app
uvicorn.run(create_web_app('/tmp/ts-web-store.json'), port=8000)
"
```

Drive the built CLI:

```bash
G=.mint/generated
PP="$PWD/$G/timesheet-cli/src:$PWD/$G/timestore/src:$PWD/$G/rules/src"
export TIMESHEET_STORE=/tmp/ts-store.json TIMESHEET_TIMER=/tmp/ts-timer.json
PYTHONPATH="$PP" python -m timesheet_cli add Apollo 2026-07-06 2.5
PYTHONPATH="$PP" python -m timesheet_cli list --week 2026-W28
```

(`-m timesheet_cli`, not a submodule path: the spec pins the package name and
`main(argv=None)`; the internal module layout is the renderer's to choose and
has changed between recordings.)

Drive the API in-process (the conformance style — never a server subprocess):

```bash
PP="$PWD/$G/timesheet-api/src:$PWD/$G/timestore/src:$PWD/$G/rules/src:$PWD/$G/timesheet-api/.mint-deps"
PYTHONPATH="$PP" python -c "
from fastapi.testclient import TestClient
from timesheet_api import create_app
c = TestClient(create_app('/tmp/ts-api-store.json'))
print(c.post('/entries', json={'project': 'Apollo',
                               'date': '2026-07-06', 'hours': 6.0}).json())
"
```

### Re-recording after a spec change (manual, calls a real model)

```bash
MINT_LIVE=1 mint render <module> --range FR1:FR1   # one unit at a time, or
MINT_LIVE=1 mint live-smoke <module>               # force a full re-record
```

Default provider is `claude-cli` / `sonnet` (uses your Claude Code auth). New
cassettes are written under `resources/cassettes/` and should be committed.

## Claude Code integration (hand-written glue)

Everything under [`claude/`](./claude) is **hand-written**, not mint-generated —
it is environment wiring, not a bounded module:

- [`claude/timesheet.sh`](./claude/timesheet.sh) — wrapper that sets
  `PYTHONPATH` to the generated modules' `src` dirs, defaults
  `TIMESHEET_STORE`/`TIMESHEET_TIMER` to `~/.timesheet/`, and execs the CLI.
- [`claude/session_start.sh`](./claude/session_start.sh) — SessionStart hook:
  runs `timesheet start` with the project from `TIMESHEET_PROJECT` or the repo
  directory name.
- [`claude/session_end.sh`](./claude/session_end.sh) — SessionEnd hook: runs
  `timesheet stop`, so every session's duration lands in the store.

The hooks are **non-blocking and fail-soft**: missing generated modules or a
timer already started by a concurrent session all exit `0` quietly. A broken
timesheet must never break a session.

### Project-scoped setup (this repo)

The repo's [`.claude/settings.json`](../../.claude/settings.json) wires both
hooks, and [`.claude/skills/timesheet/SKILL.md`](../../.claude/skills/timesheet/SKILL.md)
adds a `/timesheet` skill that prints today's and this week's logged hours.

```bash
cd examples/timesheet && mint render timesheet-cli
```

Start a Claude Code session in this repo: the timer starts; end the session and
the entry is booked. `/timesheet` shows the tally.

### User-global metering (~/.claude)

To meter every repo you work in, move the hook config to your user settings —
project dir names become the project column automatically:

```jsonc
// ~/.claude/settings.json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear",
        "hooks": [{ "type": "command", "timeout": 15,
                    "command": "$HOME/repos/mint/examples/timesheet/claude/session_start.sh" }]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [{ "type": "command", "timeout": 15,
                    "command": "$HOME/repos/mint/examples/timesheet/claude/session_end.sh" }]
      }
    ]
  }
}
```

Use an absolute path to wherever this repo is cloned (`$CLAUDE_PROJECT_DIR`
would point at whatever repo the session runs in, not this one). Copy the
skill to `~/.claude/skills/timesheet/` if you want `/timesheet` everywhere.

Notes on the moving parts:

- One timer file means one running timer: with two concurrent sessions the
  second `start` and the second `stop` are no-ops (exit 4, swallowed), so you
  meter wall-clock presence, not per-session totals.
- SessionStart fires on `startup`, `resume`, and `clear` (the matcher skips
  `compact` so mid-session compaction never starts a stray timer). SessionEnd
  fires on all termination reasons.
- Sessions shorter than the 0.1h minimum still book 0.1h — the store's
  validation rejects zero-hour entries by design.

## No real personal data

The app is single-user by design: entries carry no name, and no script reads
or guesses an identity from the OS. Specs, tests, and cassettes use obviously
fake fixtures only — projects `Apollo`/`Zephyr`.
