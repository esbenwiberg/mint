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
- Kit class: a CSS class named `ts-*` whose declarations reference tokens via `var(--ts-*)`, plain keywords, and plain size literals only.
- Page shell: a complete HTML5 document produced by `page(title, body)` that embeds the stylesheet inline.

## implementation

- Use Python 3.12 and only the standard library.
- Expose `tokens_css` and `page` from `src/ui_kit/`.
- The stylesheet lives in a private module-level constant `_TOKENS_CSS` assigned a single string literal; the public accessor `tokens_css()` takes no arguments and returns it unchanged. Keeping the literal private keeps token values out of the public interface stub, so a restyle re-renders only this module and dependents NOOP.
- The `tokens_css` docstring is exactly: `Return the ui-kit design-token stylesheet: one :root token block plus the ts- kit classes.` It never enumerates tokens or classes — the docstring is public interface, and a stable docstring keeps stylesheet edits from cascading to dependents.
- The `:root` block declares exactly these tokens: `--ts-bg: #f1f5f9`, `--ts-surface: #ffffff`, `--ts-ink: #0f172a`, `--ts-muted: #64748b`, `--ts-border: #e2e8f0`, `--ts-accent: #4f46e5`, `--ts-accent-strong: #4338ca`, `--ts-accent-soft: #eef2ff`, `--ts-ok: #059669`, `--ts-ok-soft: #ecfdf5`, `--ts-danger: #e11d48`, `--ts-danger-soft: #fff1f2`, `--ts-neutral-soft: #f1f5f9`, `--ts-radius: 12px`, `--ts-radius-sm: 8px`, `--ts-space-1: 4px`, `--ts-space-2: 8px`, `--ts-space-3: 16px`, `--ts-space-4: 24px`, `--ts-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 8px 24px rgba(15, 23, 42, 0.08)`, `--ts-ring: 0 0 0 3px #c7d2fe`, `--ts-font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`.
- Hex color literals appear only inside the `:root` block; every kit class takes its colors from `var(--ts-*)`. This is what makes live restyling cheap: the whole look can be previewed by overriding the `:root` block in browser devtools before any value is pinned here.
- `ts-body` styles the document body: zero margin, `--ts-bg` background, `--ts-ink` color, `--ts-font` family.
- `ts-header` is the app bar: `--ts-surface` background, a 1px solid `--ts-border` bottom border, horizontal padding `--ts-space-4` and vertical padding `--ts-space-3`; its heading has zero margin and a 20px font size.
- `ts-page` is the content column: 840px max width, centered with auto horizontal margins, `--ts-space-4` padding, a grid display with `--ts-space-4` row gap.
- `ts-card` is the surface block: `--ts-surface` background, `--ts-radius` corners, `--ts-shadow` shadow, `--ts-space-4` padding.
- `ts-table` fills its container with collapsed borders; header cells are left-aligned, uppercase, 12px, `--ts-muted`; body cells have `--ts-space-2` vertical and `--ts-space-3` horizontal padding and a 1px solid `--ts-border` top border; hovered body rows take a `--ts-accent-soft` background.
- `ts-badge` is a status pill: inline-block, 999px corner radius, 12px size, 600 weight, `--ts-space-1` vertical and 10px horizontal padding; the variants pair a soft background with a strong text color — `ts-badge--draft` uses `--ts-neutral-soft` with `--ts-muted`, `ts-badge--submitted` uses `--ts-accent-soft` with `--ts-accent`, `ts-badge--approved` uses `--ts-ok-soft` with `--ts-ok`, `ts-badge--rejected` uses `--ts-danger-soft` with `--ts-danger`.
- `ts-form` is a grid with `--ts-space-2` gap; `ts-toolbar` is a wrapping flex row with `--ts-space-2` gap and centered items; `ts-actions` is a flex row with `--ts-space-1` gap.
- `ts-input` has a 1px solid `--ts-border` border, `--ts-radius-sm` corners, `--ts-space-2` padding, and inherits the font; on focus it drops the outline, takes the `--ts-ring` box shadow, and a `--ts-accent` border color.
- `ts-button` is the primary action: `--ts-accent` background, `--ts-surface` text, no border, `--ts-radius-sm` corners, 600 weight, pointer cursor, inherited font, `--ts-space-2` vertical and `--ts-space-3` horizontal padding; on hover it takes `--ts-accent-strong`.
- The button variants are modifiers applied together with `ts-button` and override only colors and border: `ts-button--ghost` is transparent with `--ts-accent` text and a 1px solid `--ts-border` border, hovering to `--ts-accent-soft`; `ts-button--danger` pairs `--ts-danger-soft` background with `--ts-danger` text, hovering to `--ts-danger` background with `--ts-surface` text.
- `ts-total` is `--ts-muted` at 14px; `ts-error` pairs `--ts-danger-soft` background with `--ts-danger` text, `--ts-radius` corners, and `--ts-space-3` padding; `ts-empty` is `--ts-muted`, centered text, `--ts-space-4` padding, a 1px dashed `--ts-border` border, and `--ts-radius` corners.
- `page(title, body)` returns one HTML5 document: a `<!DOCTYPE html>` line, a viewport meta tag `width=device-width, initial-scale=1`, the HTML-escaped title inside `<title>` and inside the single top-level heading in a `<header class="ts-header">`, a single `<style>` element containing the `tokens_css()` stylesheet verbatim, a body element carrying `class="ts-body"`, and the `body` argument embedded unescaped inside `<main class="ts-page">`.
- `page` escapes only the title; callers own escaping of everything they pass as `body`.
- Unit tests use pytest.

## test

- Conformance tests use pytest and call only the public API.
- Include an empty-title page, a title needing HTML escaping, and a scan that no hex color literal appears outside the `:root` block.

## functional

- id: FR1
  title: Token stylesheet accessor
  spec:
    - `tokens_css()` returns the stylesheet declaring every token in one `:root` block and every kit class listed in the implementation requirements.
    - The returned stylesheet comes verbatim from the private `_TOKENS_CSS` literal, and the `tokens_css` docstring matches the implementation requirements exactly.
    - Kit classes reference colors only through `var(--ts-*)`; a hex color literal outside the `:root` block is invalid.
    - Interactive kit classes carry their states — `ts-input` declares a focus rule and `ts-button` declares a hover rule.
  acceptance:
    - `tokens_css()` returns a string containing `--ts-accent: #4f46e5`, `--ts-ok: #059669`, `--ts-danger: #e11d48`, `--ts-shadow:`, `--ts-space-4: 24px`, `--ts-radius: 12px`, and `--ts-font: ui-sans-serif`.
    - Calling `tokens_css()` twice returns equal strings, and `tokens_css.__doc__` stripped of surrounding whitespace equals `Return the ui-kit design-token stylesheet: one :root token block plus the ts- kit classes.`
    - `tokens_css()` contains one `.ts-badge--approved` rule whose text contains `var(--ts-ok-soft)` and `var(--ts-ok)`, and one `.ts-badge--rejected` rule whose text contains `var(--ts-danger-soft)` and `var(--ts-danger)`.
    - `tokens_css()` contains a `.ts-input:focus` rule whose text contains `var(--ts-ring)` and a `.ts-button:hover` rule whose text contains `var(--ts-accent-strong)`.
    - Splitting the `tokens_css()` string at the `:root` block's closing brace, the remainder contains no `#` followed by six hex digits — every non-root hex literal is an error.
    - `tokens_css()` contains `.ts-card`, `.ts-toolbar`, `.ts-actions`, `.ts-total`, `.ts-empty`, `.ts-error`, `.ts-button--ghost`, and `.ts-button--danger` rules.

- id: FR2
  title: Page shell
  spec:
    - `page(title, body)` returns a full HTML5 document embedding the `tokens_css()` stylesheet in a single `<style>` element and `body` unescaped inside `<main class="ts-page">`.
    - The document carries the viewport meta tag, a body element with `class="ts-body"`, and exactly one top-level heading inside `<header class="ts-header">`.
    - The title is HTML-escaped in both the `<title>` element and the heading; an empty title still returns a document containing the `<style>` element.
  acceptance:
    - `page("Timesheet", "<p>hi</p>")` returns a string that starts with `<!DOCTYPE html>`, contains the `tokens_css()` string exactly once, contains `width=device-width, initial-scale=1`, contains `class="ts-body"`, and contains `<p>hi</p>` unchanged.
    - `page("A <b> title", "")` returns a document containing `A &lt;b&gt; title` and not `<b> title`.
    - `page("", "")` returns a document containing `<main class="ts-page">`, one `<style>` element, and one `<header class="ts-header">`.
