# timesheet — session-metered timesheet backend + CLI

A small timesheet system built as a Mint spec graph, plus hand-written Claude
Code glue that meters coding sessions automatically: a SessionStart hook starts
a timer, a SessionEnd hook stops it and books the elapsed time as a draft
entry.

```bash
timesheet add "Ada Lovelace" Apollo 2026-07-06 2.5   # book time by hand
timesheet start "Ada Lovelace" Apollo                # ... or run a timer
timesheet stop                                       # -> #2 Ada Lovelace Apollo 2026-07-08 1.5h draft
timesheet submit 2                                   # draft -> submitted
```

## Module graph

`requires` edges, rendered bottom-up:

```text
timestore ─┬─> timesheet-api
rules ─────┤
           └─> timesheet-cli
```

- **timestore** — JSON-file-backed store of entries
  `{id, person, project, date, hours, status}`; CRUD plus queries by project
  and by ISO week (`2026-W28`); store path from `TIMESHEET_STORE`; typed
  `StoreError`. Stdlib only.
- **rules** — pure functions, no I/O: `validate_hours` (positive, and a
  person's day total capped at exactly 24), the approval state machine
  `draft -> submitted -> approved | rejected`, and `assert_editable` (approved
  entries are immutable); typed `RuleError`.
- **timesheet-api** — FastAPI app factory `create_app(store_path)` composing
  both. Declares `fastapi`/`httpx` in its generated `pyproject.toml`; Mint
  installs them into a module-local `.mint-deps` before tests.
- **timesheet-cli** — the `timesheet` command: `add`, `list`, `start`, `stop`,
  `status`, `submit`. Timer file path from `TIMESHEET_TIMER`; `stop` rounds to
  the nearest 0.1h (minimum 0.1) and books the entry via timestore.

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
mint report timesheet-cli
```

Drive the built CLI:

```bash
G=.mint/generated
PP="$PWD/$G/timesheet-cli/src:$PWD/$G/timestore/src:$PWD/$G/rules/src"
export TIMESHEET_STORE=/tmp/ts-store.json TIMESHEET_TIMER=/tmp/ts-timer.json
PYTHONPATH="$PP" python -m timesheet_cli.cli add "Ada Lovelace" Apollo 2026-07-06 2.5
PYTHONPATH="$PP" python -m timesheet_cli.cli list --week 2026-W28
```

Drive the API in-process (the conformance style — never a server subprocess):

```bash
PP="$PWD/$G/timesheet-api/src:$PWD/$G/timestore/src:$PWD/$G/rules/src:$PWD/$G/timesheet-api/.mint-deps"
PYTHONPATH="$PP" python -c "
from fastapi.testclient import TestClient
from timesheet_api import create_app
c = TestClient(create_app('/tmp/ts-api-store.json'))
print(c.post('/entries', json={'person': 'Ada Lovelace', 'project': 'Apollo',
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
  runs `timesheet start` with the person from `TIMESHEET_PERSON` and the
  project from `TIMESHEET_PROJECT` or the repo directory name.
- [`claude/session_end.sh`](./claude/session_end.sh) — SessionEnd hook: runs
  `timesheet stop`, so every session's duration lands in the store.

The hooks are **non-blocking and fail-soft**: no `TIMESHEET_PERSON`, missing
generated modules, or a timer already started by a concurrent session all exit
`0` quietly. A broken timesheet must never break a session.

### Project-scoped setup (this repo)

The repo's [`.claude/settings.json`](../../.claude/settings.json) wires both
hooks, and [`.claude/skills/timesheet/SKILL.md`](../../.claude/skills/timesheet/SKILL.md)
adds a `/timesheet` skill that prints today's and this week's logged hours.

```bash
export TIMESHEET_PERSON="Ada Lovelace"   # identity comes from env vars only
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
would point at whatever repo the session runs in, not this one). Set
`TIMESHEET_PERSON` in your shell profile. Copy the skill to
`~/.claude/skills/timesheet/` if you want `/timesheet` everywhere.

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

Specs, tests, and cassettes use obviously fake fixtures only — `Ada Lovelace`,
projects `Apollo`/`Zephyr`. The hook scripts read identity from env vars only;
nothing guesses a username from the OS.
