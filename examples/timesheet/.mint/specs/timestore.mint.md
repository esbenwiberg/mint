---
module: timestore
description: JSON-file-backed store of timesheet entries
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- Entry: a plain dict with keys `id`, `person`, `project`, `date`, `hours`, `status`.
- Store file: a JSON file holding every entry; its path comes from an explicit constructor argument or the `TIMESHEET_STORE` environment variable.
- ISO week: a week label written `YYYY-Www` with a zero-padded week number, for example `2026-W28`.
- StoreError: the typed error for store misuse — a missing store path, an invalid date, an invalid week label, or an unknown entry id.

## implementation

- Use Python 3.12 and only the standard library.
- Expose `TimeStore` and `StoreError` from `src/timestore/`.
- `TimeStore(path=None)` uses the explicit path when given; otherwise it reads the `TIMESHEET_STORE` environment variable at construction time. With neither set, the constructor raises `StoreError` immediately.
- A missing store file means an empty store; the file and its parent directories are created on the first write.
- `add_entry(person, project, date, hours)` assigns the next integer id starting at 1, stores the entry with status `draft`, persists it, and returns the stored entry.
- Expose `get_entry(entry_id)`, `update_entry(entry_id, **fields)`, `delete_entry(entry_id)`, and `list_entries()`; `update_entry` merges the given fields into the stored entry, persists, and returns the updated entry.
- `entries_for_project(project)` returns the entries whose `project` equals the given name, in insertion order.
- `entries_for_week(week)` returns the entries whose `date` falls in the given ISO week, using the ISO 8601 calendar for year boundaries.
- Dates are strings `YYYY-MM-DD`; `add_entry` and `update_entry` raise `StoreError` at call time for a value that is not a valid calendar date in that format.
- `get_entry`, `update_entry`, and `delete_entry` raise `StoreError` at call time when no entry has the given id.
- `entries_for_week` raises `StoreError` at call time for a week label not in the `YYYY-Www` form.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public API and point every store at a `tmp_path` file.
- Include round-trip persistence, unknown-id errors, invalid dates, and an ISO year-boundary week query.

## functional

- id: FR1
  title: Create, persist, and read entries
  spec:
    - `TimeStore(path)` writes entries to the JSON store file at `path`, and a new `TimeStore` on the same path reads them back.
    - With `path=None` the store path comes from the `TIMESHEET_STORE` environment variable, read at construction time; with neither set, the constructor raises `StoreError` immediately.
    - `add_entry` stores status `draft` and assigns integer ids in increasing order starting at 1.
    - An invalid date raises `StoreError` at call time, before anything is stored.
  acceptance:
    - `add_entry("Ada Lovelace", "Apollo", "2026-07-06", 2.0)` returns an entry whose `id` equals 1 and whose `status` equals `draft`.
    - After that add, a new `TimeStore` constructed with the same path returns the same entry from `get_entry(1)`.
    - With `TIMESHEET_STORE` set to a temp file path, `TimeStore()` stores entries at that path.
    - With no path argument and `TIMESHEET_STORE` unset, `TimeStore()` raises `StoreError` whose message contains `store path`.
    - `add_entry("Ada Lovelace", "Apollo", "2026-13-40", 1.0)` raises `StoreError` whose message contains `invalid date`.

- id: FR2
  title: Update, delete, and unknown-id errors
  spec:
    - `update_entry(entry_id, **fields)` merges the given fields into the stored entry and persists the change; `delete_entry(entry_id)` removes the entry.
    - `get_entry`, `update_entry`, and `delete_entry` raise `StoreError` at call time when no entry has the given id.
    - `update_entry` raises `StoreError` at call time for an invalid replacement date and leaves the entry unmodified.
  acceptance:
    - After `update_entry(1, hours=3.5)`, `get_entry(1)` returns an entry whose `hours` equals 3.5 and whose `person` still equals `Ada Lovelace`.
    - After `delete_entry(1)`, `get_entry(1)` raises `StoreError`.
    - `get_entry(99)` on a store holding no entry 99 raises `StoreError` whose message contains `unknown entry`.
    - `update_entry(1, date="2026-02-30")` raises `StoreError` whose message contains `invalid date`, and `get_entry(1)` still returns the original date.

- id: FR3
  title: Query by project and by ISO week
  spec:
    - `entries_for_project(project)` returns only the entries whose `project` equals the given name.
    - `entries_for_week(week)` returns the entries whose `date` falls in the ISO week labelled `YYYY-Www`; ISO year boundaries follow the ISO 8601 calendar.
    - A week label not in the `YYYY-Www` form raises `StoreError` at call time.
  acceptance:
    - With entries stored for projects `Apollo` and `Zephyr`, `entries_for_project("Apollo")` returns only the `Apollo` entries.
    - With an entry dated `2026-07-06`, `entries_for_week("2026-W28")` returns that entry and `entries_for_week("2026-W27")` returns an empty list.
    - An entry dated `2027-01-01` is returned by `entries_for_week("2026-W53")`, because that date belongs to ISO year 2026.
    - `entries_for_week("2026-28")` raises `StoreError` whose message contains `invalid week`.
