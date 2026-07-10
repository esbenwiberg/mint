---
module: timesheet-web
description: Server-rendered HTML front end for the personal timesheet, skinned by ui-kit
imports: [timestore, rules, ui-kit]
requires: [timestore, rules, ui-kit]
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- App factory: `create_web_app(store_path)` returning a FastAPI application bound to one store file.
- Entries page: the HTML document served at `GET /` listing entries and hosting the filter toolbar and the add form.
- Entry row: a `<tr>` carrying `data-testid="entry-row"` that shows one entry's id, project, date, hours, status badge, and actions.
- Filter form: the GET `<form>` carrying `data-testid="filter-form"` and the class `ts-toolbar`, laying its `week` and `project` text inputs and its submit button out in one row.
- Add form: the `<form>` carrying `data-testid="add-form"` inside a `ts-card` that posts fields `project`, `date`, and `hours` to `POST /entries`.
- Error page: an HTML response rendered through the ui-kit page shell whose body contains a `ts-error` element naming the problem.
- Style lock: every HTML response is built with the ui-kit `page` shell, contains no `style=` attribute, and every `class` attribute names only `ts-`-prefixed kit classes.

## implementation

- Use Python 3.12.
- Expose `create_web_app(store_path)` from `src/timesheet_web/`.
- Declare `fastapi`, `httpx`, and `python-multipart` in the generated `pyproject.toml` `[project]` dependencies.
- Persist entries only through the required timestore module and enforce every rule only through the required rules module; the app holds no state of its own.
- Build every HTML response — the entries page and every error page — by passing body markup to the required ui-kit module's `page` with the title `Timesheet`; never emit a `<style>` element, a `style=` attribute, a heading of the app's own, or a class outside the `ts-` kit classes.
- HTML-escape every store-sourced value (project, date, status) with `html.escape` before it enters body markup.
- `GET /` renders the entries page: the filter form, then a `ts-card` holding the entries table with one entry row per matching entry in insertion order and a `ts-total` element stating the sum of the listed rows' hours with one decimal, then the add form; when no entries match, a `ts-empty` element replaces the table and the total.
- Each entry row shows its status in a `ts-badge ts-badge--{status}` span and its legal actions in a `ts-actions` cell: a `draft` row has one form posting to `POST /entries/{entry_id}/submit`; a `submitted` row has two forms posting to `/approve` and `/reject`; `approved` and `rejected` rows have no action forms.
- Every button composes the base class with its variant: submit and approve buttons carry `class="ts-button"`, the filter button carries `class="ts-button ts-button--ghost"`, and the reject button carries `class="ts-button ts-button--danger"`; a variant class never appears without `ts-button`.
- `GET /` accepts optional `week` (ISO `YYYY-Www`) and `project` query parameters that combine to filter the listed rows and prefill the filter form inputs; a malformed week label responds 422 with an error page.
- `POST /entries` accepts form-encoded fields `project`, `date`, and `hours`, validates the date format, checks `validate_hours` against the total hours already stored for that date read from the store, stores a new `draft` entry, and redirects with status 303 to `/`.
- Each action route moves the entry through the rules `transition`, persists the new status, and redirects with status 303 to `/`.
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
    - `GET /` responds 200 with the entries page: one entry row per stored entry with a `ts-badge--{status}` span, a `ts-total` element stating the sum of listed hours, and the add form.
    - The page shell carries the only heading; with no entries stored the page contains a `ts-empty` element and no entry row and no `ts-total`.
    - Store-sourced text is HTML-escaped in the page.
    - The style lock holds on every response: the ui-kit stylesheet appears exactly once, no `style=` attribute appears, and every `class` attribute names only `ts-` classes.
  acceptance:
    - With entries of 2.5 and 6.0 hours stored on `Apollo`, `GET /` responds 200, contains two `data-testid="entry-row"` occurrences, a `ts-badge--draft` span, and a `ts-total` element whose text contains `8.5`.
    - On an empty store, `GET /` responds 200, contains a `ts-empty` element and `data-testid="add-form"`, and contains no `data-testid="entry-row"` and no `ts-total`.
    - After adding an entry whose project is `<b>Ops</b>`, the page contains `&lt;b&gt;Ops&lt;/b&gt;` and does not contain `<b>Ops</b>`.
    - The `GET /` page contains `TOKENS_CSS` exactly once, contains no `style="` substring, contains exactly one `<h1`, and every `class="..."` attribute value consists only of names starting with `ts-`.

- id: FR2
  title: Filter the page by week and project
  spec:
    - The entries page contains the filter form; submitting it issues `GET /` with `week` and `project` query parameters that combine to filter the listed rows, and the parameters prefill the form inputs.
    - The `ts-total` element states the sum of the filtered rows only.
    - A malformed week label responds 422 with an error page; a filter that matches nothing renders the `ts-empty` element instead of the table.
  acceptance:
    - With an `Apollo` entry of 2.5 hours dated `2026-07-06` and a `Zephyr` entry of 6.0 hours dated `2026-06-29`, `GET /?week=2026-W28` responds 200, contains `Apollo`, contains no `Zephyr` entry row, and its `ts-total` text contains `2.5`.
    - `GET /?project=Zephyr` responds 200, contains exactly one `data-testid="entry-row"`, and contains an input whose `value="Zephyr"`.
    - `GET /?week=2026-W27&project=Apollo` responds 200 and contains a `ts-empty` element and no entry row.
    - `GET /?week=28` responds 422 and the page contains a `ts-error` element whose text contains `week`.
    - The `GET /` page contains `data-testid="filter-form"` with inputs named `week` and `project` and a button whose class equals `ts-button ts-button--ghost`.

- id: FR3
  title: Add entries through the form
  spec:
    - `POST /entries` with valid form fields stores a `draft` entry and responds 303 with a `location` header of `/`.
    - Form values whose hours fail `validate_hours` — zero, negative, or lifting the date's stored total beyond the 24-hour cap — respond 422 with an error page and store nothing.
    - An invalid date responds 422 with an error page and stores nothing.
  acceptance:
    - `POST /entries` with fields `project=Apollo`, `date=2026-07-06`, `hours=2.5` responds 303 with a `location` header equal to `/`, and a following `GET /` contains an entry row with `2.5`.
    - `POST /entries` with `hours=0` responds 422 and the page contains a `ts-error` element whose text contains `hours`, and a following `GET /` contains no new entry row.
    - With 20.0 hours already stored on `2026-07-06`, posting 4.0 more hours responds 303 — the exact 24-hour day boundary — and posting 4.5 instead responds 422 and stores nothing.
    - `POST /entries` with `date=2026-13-40` responds 422 and the page contains a `ts-error` element whose text contains `date`.

- id: FR4
  title: Move entries through the workflow from the page
  spec:
    - Entry rows offer only their legal actions: a `draft` row one submit form, a `submitted` row an approve form and a reject form, and `approved` and `rejected` rows none.
    - A legal action persists the new status via the rules `transition` and responds 303 with a `location` header of `/`.
    - An unknown entry id responds 404 with an error page; an illegal transition responds 409 with an error page and leaves the stored status unmodified.
  acceptance:
    - With draft entry 1 stored, the `GET /` page contains `action="/entries/1/submit"` and contains neither `action="/entries/1/approve"` nor `action="/entries/1/reject"`.
    - `POST /entries/1/submit` responds 303, and the following `GET /` contains a `ts-badge--submitted` span, `action="/entries/1/approve"`, `action="/entries/1/reject"`, and a button whose class equals `ts-button ts-button--danger`.
    - After `POST /entries/1/approve`, the following `GET /` contains a `ts-badge--approved` span and no `action="/entries/1/` substring.
    - `POST /entries/999/submit` responds 404 and the page contains a `ts-error` element whose text contains `unknown`.
    - `POST /entries/2/approve` while entry 2 is still `draft` responds 409, and a following `GET /` still contains a `ts-badge--draft` span.
