---
module: timesheet-api
description: FastAPI backend exposing the personal timesheet store and rules over HTTP
imports: [timestore, rules]
requires: [timestore, rules]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- App factory: `create_app(store_path)` returning a FastAPI application bound to one store file.
- Entry body: a JSON object with keys `project`, `date`, `hours`.
- Week query: an ISO week label `YYYY-Www` passed as the `week` query parameter.
- Error contract: 422 for an invalid body or rule violation, 404 for an unknown entry or project, 409 for an illegal transition or an edit of an approved entry.

## implementation

- Use Python 3.12.
- Expose `create_app(store_path)` from `src/timesheet_api/`.
- Declare `fastapi` and `httpx` in the generated `pyproject.toml` `[project]` dependencies.
- Persist entries only through the required timestore module and enforce every rule only through the required rules module; the app holds no state of its own.
- POST `/entries` validates the body, checks `validate_hours` against the total hours already stored for the body's date read from the store, and stores a new `draft` entry.
- GET `/projects/{project}/entries` returns the project's entries, filtered to the `week` query parameter when one is given; a malformed week label responds 422.
- POST `/entries/{entry_id}/submit`, `/approve`, and `/reject` move the entry through the rules state machine and persist the new status.
- PUT `/entries/{entry_id}` replaces `project`, `date`, and `hours` on an entry after `assert_editable` and `validate_hours` pass; the entry keeps its status.
- Map errors at request time: an invalid body shape, date, or rule violation responds 422; an unknown entry id or unknown project responds 404; a `RuleError` from a status transition or from editing an approved entry responds 409. Every error response carries a JSON `detail` field.
- A project is unknown when the store holds no entries for it at all; a known project with no entries in the requested week responds 200 with an empty list.
- Unit tests use pytest.

## test

- Conformance tests use pytest and drive the app in-process with `fastapi.testclient.TestClient`; never start a server subprocess.
- Conformance tests point each app at a `tmp_path` store file via `create_app(store_path)`.
- Include every status code in the error contract: 201, 200, 422, 404, and 409.

## functional

- id: FR1
  title: Create entries with validation
  spec:
    - POST `/entries` with a valid body stores a `draft` entry and responds 201 with the stored entry including its integer `id`.
    - A body whose hours fail `validate_hours` — zero, negative, or lifting the date's stored total beyond the 24-hour cap — responds 422.
    - A body missing keys or carrying an invalid `date` responds 422 and stores nothing.
  acceptance:
    - POST `/entries` with `{"project": "Apollo", "date": "2026-07-06", "hours": 6.0}` responds 201 and the body contains `"status": "draft"` and an integer `id`.
    - POST `/entries` with `hours` 0 responds 422.
    - With 20.0 hours already stored on `2026-07-06`, posting 4.0 more hours responds 201 — the exact 24-hour day boundary — and posting 4.5 instead responds 422.
    - POST `/entries` with `date` `2026-13-40` responds 422.

- id: FR2
  title: Query a project's entries by ISO week
  spec:
    - GET `/projects/{project}/entries` responds 200 with all of the project's entries.
    - With `?week=YYYY-Www` the list is filtered to entries whose date falls in that ISO week; a malformed week label responds 422.
    - A project with no stored entries responds 404; a known project with no entries in the requested week responds 200 with an empty list.
  acceptance:
    - With one `Apollo` entry dated `2026-07-06`, GET `/projects/Apollo/entries` responds 200 and the body contains that entry.
    - GET `/projects/Apollo/entries?week=2026-W28` responds 200 with the entry, and `?week=2026-W27` responds 200 with an empty JSON list.
    - GET `/projects/Nonexistent/entries` responds 404.
    - GET `/projects/Apollo/entries?week=28` responds 422.

- id: FR3
  title: Submit, approve, and reject entries
  spec:
    - POST `/entries/{entry_id}/submit` moves `draft` to `submitted`; `/approve` and `/reject` move `submitted` to `approved` and `rejected`; each responds 200 with the updated entry.
    - An unknown entry id responds 404.
    - An illegal transition responds 409 and leaves the stored status unmodified.
  acceptance:
    - After creating a draft entry with id 1, POST `/entries/1/submit` responds 200 and the body contains `"status": "submitted"`.
    - After that submit, POST `/entries/1/approve` responds 200 and the body contains `"status": "approved"`.
    - POST `/entries/999/submit` responds 404.
    - POST `/entries/1/approve` while entry 1 is still `draft` responds 409, and a following GET shows the entry still `draft`.

- id: FR4
  title: Edit entries until approval
  spec:
    - PUT `/entries/{entry_id}` replaces `project`, `date`, and `hours` and responds 200 with the updated entry; the status is kept.
    - Editing an `approved` entry responds 409.
    - The replacement body passes the same 422 validation as creation; an unknown entry id responds 404.
  acceptance:
    - PUT `/entries/1` changing `hours` to 2.5 responds 200 and the body's `hours` equals 2.5 while its `status` equals the status it had before the edit.
    - After entry 1 is approved, PUT `/entries/1` responds 409.
    - PUT `/entries/999` responds 404.
    - PUT `/entries/1` with `hours` -1.0 responds 422.
