---
module: timesheet-cli
description: Command-line personal timesheet client with a running-timer workflow
imports: [timestore, rules]
requires: [timestore, rules]
stack: python-cli
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- CLI: the `timesheet` console command.
- Timer file: a JSON object with keys `project` and `started` marking one running timer; its path comes from the `TIMESHEET_TIMER` environment variable.
- Exit code: 0 success, 2 usage or validation error, 3 unknown entry id, 4 timer-state error.

## implementation

- Use Python 3.12.
- Provide a package `timesheet_cli` with `main(argv=None)` and a console command named `timesheet`.
- Persist entries through the required timestore module — the store reads its path from `TIMESHEET_STORE` — and validate through the required rules module.
- Subcommands: `add <project> <date> <hours>`, `list [--project NAME] [--week YYYY-Www]`, `start <project>`, `stop`, `status`, `submit <id>`.
- When a subcommand needs `TIMESHEET_STORE` or `TIMESHEET_TIMER` and the variable is unset, print a message naming the variable on stderr and exit 2 before touching any file.
- `add` validates the date format itself and calls `validate_hours` with the total hours already stored for that date, then stores a `draft` entry and prints it with its id.
- `list` prints one line per entry containing id, project, date, hours, and status, filtered by `--project` and `--week` when given.
- `start` writes the timer file with the project and the current UTC time as an ISO 8601 `started` timestamp.
- `stop` reads the timer file, computes elapsed hours from `started` to the current UTC time, rounds to the nearest 0.1 hour and never below 0.1, stores a `draft` entry dated on the `started` UTC date, deletes the timer file, and prints the stored entry.
- `status` prints the running timer's project and `started` timestamp, or `no running timer` when the file is absent, and exits 0 either way.
- `submit` moves the entry from `draft` to `submitted` via the rules `transition` and persists the new status.
- Map errors at call time: argparse usage errors, an invalid date or week argument, and any `RuleError` exit 2; a `StoreError` for an unknown entry id exits 3; starting while a timer file exists and stopping without one exit 4. Errors print on stderr; normal output prints on stdout.
- Unit tests use pytest.

## test

- Conformance tests use pytest and drive the CLI as a subprocess.
- Conformance tests set `TIMESHEET_STORE` and `TIMESHEET_TIMER` to `tmp_path` files; timer tests write the timer file directly with a known `started` timestamp.
- Include a success path and each of exit codes 2, 3, and 4.

## functional

- id: FR1
  title: Add and list entries
  spec:
    - `timesheet add Apollo 2026-07-06 2.5` stores a `draft` entry and prints it with its id.
    - `timesheet list` prints every entry; `--project` and `--week` filter the printed lines.
    - Invalid hours or an invalid date exit 2.
  acceptance:
    - After that add, `timesheet list` exits 0 and prints a line containing `Apollo` and `2.5`.
    - `timesheet list --project Zephyr` prints no `Apollo` line.
    - `timesheet list --week 2026-W28` prints the entry dated `2026-07-06`, and `timesheet list --week 2026-W27` prints no entry line.
    - `timesheet add Apollo 2026-07-06 0` exits 2 and prints a message containing `hours` on stderr.
    - `timesheet add Apollo 2026-13-40 1.0` exits 2 and prints a message containing `date` on stderr.

- id: FR2
  title: Submit entries
  spec:
    - `timesheet submit <id>` moves a `draft` entry to `submitted` and prints the updated entry.
    - An unknown entry id exits 3; a submit that is not a legal transition exits 2.
  acceptance:
    - After adding entry 1, `timesheet submit 1` exits 0 and prints a line containing `submitted`.
    - `timesheet submit 999` exits 3 and prints a message containing `unknown` on stderr.
    - Running `timesheet submit 1` a second time exits 2.

- id: FR3
  title: Start, inspect, and stop a running timer
  spec:
    - `timesheet start Apollo` writes the timer file with an ISO 8601 UTC `started` timestamp and prints the project name.
    - `timesheet status` prints the running timer's project and `started`, or `no running timer` when the file is absent, and exits 0 either way.
    - `timesheet stop` stores a `draft` entry whose hours are the elapsed time rounded to the nearest 0.1 hour and never below 0.1, dated on the `started` UTC date, deletes the timer file, and prints the stored entry.
  acceptance:
    - After `timesheet start Apollo`, the timer file contains `Apollo`, and `timesheet status` exits 0 and prints `Apollo`.
    - With a timer file whose `started` is 90 minutes in the past, `timesheet stop` exits 0 and prints an entry whose hours equal 1.5.
    - With a timer file whose `started` is 30 seconds in the past, `timesheet stop` stores an entry whose hours equal 0.1 — the minimum bookable duration.
    - After `timesheet stop`, `timesheet status` prints `no running timer` and the timer file no longer exists.

- id: FR4
  title: Timer state errors
  spec:
    - `timesheet start` while a timer file exists exits 4 and leaves the existing timer file unmodified.
    - `timesheet stop` with no timer file exits 4.
  acceptance:
    - After `timesheet start Apollo`, a second `timesheet start Zephyr` exits 4, prints a message containing `already running` on stderr, and the timer file still contains `Apollo`.
    - With no timer file, `timesheet stop` exits 4 and prints a message containing `no running timer` on stderr.
