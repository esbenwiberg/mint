# odq — query DSL to OData `$filter`

`odq` compiles a small, readable query DSL into a correctly escaped and
parenthesized OData v4 `$filter` string for the Dataverse Web API.

```text
odq "status = 'Active' and enddate < 2026-01-01"
# -> (status eq 'Active') and (enddate lt 2026-01-01)
```

## The DSL

- **Comparisons:** `=`, `!=`, `<`, `<=`, `>`, `>=`
- **Booleans:** `and`, `or`, `not`, with parentheses to override precedence
  (`not` binds tighter than `and`, which binds tighter than `or`)
- **Functions:** `contains(field, 'x')`, `startswith(field, 'x')`
- **Literals:** single-quoted strings (`'Active'`), integers and decimals,
  ISO dates (`2026-01-01`), and the booleans `true` / `false`

### Compiles to OData

| DSL | OData |
|-----|-------|
| `=` `!=` `<` `<=` `>` `>=` | `eq` `ne` `lt` `le` `gt` `ge` |
| `and` / `or` | `and` / `or` (each group parenthesized) |
| `not X` | `not (X)` |
| `'O''Brien'` | `'O''Brien'` (single quotes doubled) |
| `contains(name, 'Ac')` | `contains(name,'Ac')` |

## Module graph

`requires` edges, rendered bottom-up:

```text
query-lexer  ->  query-parser  ->  odata-emitter  ->  odq-cli
```

- **query-lexer** — tokenizes the DSL; raises `LexError` on bad literals.
- **query-parser** — builds the filter AST; raises `ParseError` on syntax errors.
- **odata-emitter** — exposes `compile_filter(text)`, maps the AST to OData, and
  raises `EmitError` for unsupported operators/functions. Re-exports `LexError`
  and `ParseError` so the CLI can map every error from one import.
- **odq-cli** — the `odq` command; maps each typed error to a distinct exit code.

### Typed errors and exit codes

| Condition | Error | Exit code |
|-----------|-------|-----------|
| success | — | `0` |
| syntax error | `ParseError` | `2` |
| unknown operator / function | `EmitError` | `3` |
| bad literal | `LexError` | `4` |

## Render this graph (one-time live record, then offline)

These are template-free model specs, so the first render records cassettes via a
live provider (`claude-cli` / `sonnet` by default — uses your Claude Code auth).
Run bottom-up from this directory:

```bash
cd examples/odq

# 1. validate offline (already green)
for m in query-lexer query-parser odata-emitter odq-cli; do mint lint "$m"; done

# 2. record cassettes live, bottom-up (manual: calls a real model)
MINT_LIVE=1 mint live-smoke query-lexer
MINT_LIVE=1 mint live-smoke query-parser
MINT_LIVE=1 mint live-smoke odata-emitter
MINT_LIVE=1 mint live-smoke odq-cli

# 3. from now on, render/replay is offline
mint render odq-cli
mint status odq-cli
mint report odq-cli
```

Recorded cassettes are written under `resources/cassettes/` and are meant to be
committed next to the specs.
