---
module: timesheet-web
description: Server-rendered HTML front end for the timesheet store, skinned by ui-kit
imports: [timestore, rules, ui-kit]
requires: [timestore, rules, ui-kit]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- App factory: `create_web_app(store_path)` returning a FastAPI application bound to one store file.
- Entries page: the HTML document served at `GET /` listing entries and hosting the add form.
- Entry row: a `<tr>` carrying `data-testid="entry-row"` that shows one entry's id, person, project, date, hours, and status badge.
- Add form: the `<form>` carrying `data-testid="add-form"` that posts fields `person`, `project`, `date`, and `hours` to `POST /entries`.
- Error page: an HTML response rendered through the ui-kit page shell whose body contains a `ts-error` element naming the problem.
- Style lock: every HTML response is built with the ui-kit `page` shell, contains no `style=` attribute, and every `class` attribute names only `ts-`-prefixed kit classes.

## implementation

- Use Python 3.12.
- Expose `create_web_app(store_path)` from `src/timesheet_web/`.
- Declare `fastapi`, `httpx`, and `python-multipart` in the generated `pyproject.toml` `[project]` dependencies.
- Persist entries only through the required timestore module and enforce every rule only through the required rules module; the app holds no state of its own.
- Build every HTML response — the entries page and every error page — by passing body markup to the required ui-kit module's `page`; never emit a `<style>` element, a `style=` attribute, or a class outside the `ts-` kit classes.
- HTML-escape every store-sourced value (person, project, date, status) with `html.escape` before it enters body markup.
- `GET /` renders the entries page: one entry row per stored entry in insertion order, each status shown in a `ts-badge ts-badge--{status}` span, the add form, and a `ts-empty` element instead of the table when no entries match.
- `GET /` accepts optional `week` (ISO `YYYY-Www`) and `project` query parameters that filter the listed rows; a malformed week label responds 422 with an error page.
- `POST /entries` accepts form-encoded fields, validates the date format, checks `validate_hours` against the person's existing day total read from the store, stores a new `draft` entry, and redirects with status 303 to `/`.
- Each entry row contains three inline forms posting to `POST /entries/{entry_id}/submit`, `/approve`, and `/reject`; each action moves the entry through the rules state machine, persists the new status, and redirects with status 303 to `/`.
- Map errors at request time to error pages: an invalid form value or rule violation responds 422, an unknown entry id responds 404, and an illegal status transition responds 409; nothing is stored or modified on an error response.
- Unit tests use pytest.

## test

- Conformance tests use pytest and drive the app in-process with `fastapi.testclient.TestClient`; never start a server subprocess.
- Conformance tests point each app at a `tmp_path` store file via `create_web_app(store_path)` and disable redirect following when asserting 303 responses.
- Conformance tests assert markup with string containment and `re` over the response text, and import `TOKENS_CSS` from the required ui-kit module for the style-lock checks.
- Include every status code in the contract: 200, 303, 404, 409, and 422.

## functional

- id: FR1
  title: Entries page and style lock
  spec:
    - `GET /` responds 200 with the entries page: one entry row per stored entry, each with a `ts-badge--{status}` span, plus the add form.
    - With no entries stored the page contains a `ts-empty` element and no entry row.
    - Store-sourced text is HTML-escaped in the page.
    - The style lock holds on every response: the ui-kit stylesheet appears exactly once, no `style=` attribute appears, and every `class` attribute names only `ts-` classes.
  acceptance:
    - With one stored entry for `Ada Lovelace` on `Apollo`, `GET /` responds 200 and the page contains one `data-testid="entry-row"`, the text `Ada Lovelace`, and a `ts-badge--draft` span.
    - On an empty store, `GET /` responds 200, contains a `ts-empty` element and `data-testid="add-form"`, and contains no `data-testid="entry-row"`.
    - After adding an entry whose project is `<b>Ops</b>`, the page contains `&lt;b&gt;Ops&lt;/b&gt;` and does not contain `<b>Ops</b>`.
    - The `GET /` page contains `TOKENS_CSS` exactly once, contains no `style="` substring, and every `class="..."` attribute value consists only of names starting with `ts-`.

- id: FR2
  title: Filter the page by week and project
  spec:
    - `GET /?week=YYYY-Www` lists only the entry rows whose date falls in that ISO week; `GET /?project=NAME` lists only that project's rows; both parameters combine.
    - A malformed week label responds 422 with an error page.
    - A filter that matches nothing renders the `ts-empty` element instead of the table.
  acceptance:
    - With an `Apollo` entry dated `2026-07-06` and a `Zephyr` entry dated `2026-06-29`, `GET /?week=2026-W28` responds 200, contains `Apollo`, and contains no `Zephyr` entry row.
    - `GET /?project=Zephyr` responds 200 and contains exactly one `data-testid="entry-row"`.
    - `GET /?week=2026-W27&project=Apollo` responds 200 and contains a `ts-empty` element and no entry row.
    - `GET /?week=28` responds 422 and the page contains a `ts-error` element whose text contains `week`.

- id: FR3
  title: Add entries through the form
  spec:
    - `POST /entries` with valid form fields stores a `draft` entry and responds 303 with a `location` header of `/`.
    - Form values whose hours fail `validate_hours` — zero, negative, or lifting the person's day total beyond the 24-hour cap — respond 422 with an error page and store nothing.
    - An invalid date responds 422 with an error page and stores nothing.
  acceptance:
    - `POST /entries` with fields `person=Ada Lovelace`, `project=Apollo`, `date=2026-07-06`, `hours=2.5` responds 303 with a `location` header equal to `/`, and a following `GET /` contains an entry row with `2.5`.
    - `POST /entries` with `hours=0` responds 422 and the page contains a `ts-error` element whose text contains `hours`, and a following `GET /` contains no new entry row.
    - With 20.0 hours already stored for `Ada Lovelace` on `2026-07-06`, posting 4.0 more hours responds 303 — the exact 24-hour day boundary — and posting 4.5 instead responds 422 and stores nothing.
    - `POST /entries` with `date=2026-13-40` responds 422 and the page contains a `ts-error` element whose text contains `date`.

- id: FR4
  title: Move entries through the workflow from the page
  spec:
    - Every entry row contains three inline forms posting to that entry's `submit`, `approve`, and `reject` action URLs.
    - A legal action persists the new status via the rules `transition` and responds 303 with a `location` header of `/`.
    - An unknown entry id responds 404 with an error page; an illegal transition responds 409 with an error page and leaves the stored status unmodified.
  acceptance:
    - With entry 1 stored, the `GET /` page contains `action="/entries/1/submit"`, `action="/entries/1/approve"`, and `action="/entries/1/reject"`.
    - `POST /entries/1/submit` on a draft entry responds 303, and a following `GET /` contains a `ts-badge--submitted` span.
    - `POST /entries/999/submit` responds 404 and the page contains a `ts-error` element whose text contains `unknown`.
    - `POST /entries/1/approve` while entry 1 is still `draft` responds 409, and a following `GET /` still contains a `ts-badge--draft` span.
