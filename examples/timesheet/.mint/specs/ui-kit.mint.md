---
module: ui-kit
description: Design tokens and HTML page shell shared by every timesheet UI
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: sonnet
rendererPromptVersion: timesheet-v1
---

## definitions

- Token: a CSS custom property named `--ts-*` declared once inside the `:root` block of the stylesheet.
- Kit class: a CSS class named `ts-*` whose declarations reference tokens via `var(--ts-*)` and plain keywords only.
- Page shell: a complete HTML5 document produced by `page(title, body)` that embeds the stylesheet inline.

## implementation

- Use Python 3.12 and only the standard library.
- Expose `TOKENS_CSS` and `page` from `src/ui_kit/`.
- `TOKENS_CSS` is a module-level string constant assigned a single string literal, so dependents see the full stylesheet in the public interface stub.
- The `:root` block declares exactly these tokens: `--ts-bg: #f8fafc`, `--ts-surface: #ffffff`, `--ts-ink: #0f172a`, `--ts-muted: #64748b`, `--ts-accent: #2563eb`, `--ts-ok: #16a34a`, `--ts-danger: #dc2626`, `--ts-radius: 8px`, `--ts-space: 8px`, `--ts-font: system-ui, sans-serif`.
- Hex color literals appear only inside the `:root` block; every kit class uses `var(--ts-*)` for colors.
- Define kit classes `ts-page`, `ts-header`, `ts-table`, `ts-badge`, `ts-badge--draft`, `ts-badge--submitted`, `ts-badge--approved`, `ts-badge--rejected`, `ts-form`, `ts-input`, `ts-button`, `ts-error`, and `ts-empty`.
- Status badge colors map draft to `--ts-muted`, submitted to `--ts-accent`, approved to `--ts-ok`, and rejected to `--ts-danger`.
- `page(title, body)` returns one HTML5 document: a `<!DOCTYPE html>` line, the HTML-escaped title inside `<title>` and inside an `<h1>` in a `<header class="ts-header">`, a single `<style>` element containing `TOKENS_CSS` verbatim, and the `body` argument embedded unescaped inside `<main class="ts-page">`.
- `page` escapes only the title; callers own escaping of everything they pass as `body`.
- Unit tests use pytest.

## test

- Conformance tests use pytest and call only the public API.
- Include an empty-title page, a title needing HTML escaping, and a scan that no hex color literal appears outside the `:root` block.

## functional

- id: FR1
  title: Token stylesheet constant
  spec:
    - `TOKENS_CSS` declares every token in one `:root` block and every kit class listed in the implementation requirements.
    - Kit classes reference colors only through `var(--ts-*)`; a hex color literal outside the `:root` block is invalid.
  acceptance:
    - `TOKENS_CSS` contains `--ts-accent: #2563eb`, `--ts-ok: #16a34a`, `--ts-danger: #dc2626`, and `--ts-font: system-ui, sans-serif`.
    - `TOKENS_CSS` contains one `.ts-badge--approved` rule whose text contains `var(--ts-ok)` and one `.ts-badge--rejected` rule whose text contains `var(--ts-danger)`.
    - Splitting `TOKENS_CSS` at the `:root` block's closing brace, the remainder contains no `#` followed by six hex digits — every non-root hex literal is an error.
    - `TOKENS_CSS` contains `.ts-empty` and `.ts-error` rules.

- id: FR2
  title: Page shell
  spec:
    - `page(title, body)` returns a full HTML5 document embedding `TOKENS_CSS` in a single `<style>` element and `body` unescaped inside `<main class="ts-page">`.
    - The title is HTML-escaped in both the `<title>` element and the heading; an empty title still returns a document containing the `<style>` element.
  acceptance:
    - `page("Timesheet", "<p>hi</p>")` returns a string that starts with `<!DOCTYPE html>`, contains `TOKENS_CSS` exactly once, and contains `<p>hi</p>` unchanged.
    - `page("A <b> title", "")` returns a document containing `A &lt;b&gt; title` and not `<b> title`.
    - `page("", "")` returns a document containing `<main class="ts-page">` and one `<style>` element.
