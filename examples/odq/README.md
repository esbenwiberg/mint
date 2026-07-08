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

## Render this graph

Cassettes are already recorded and committed under `resources/cassettes/`, so
rendering replays offline — no provider, no network:

```bash
cd examples/odq
mint render odq-cli        # replays the whole graph in dependency order
mint report odq-cli
```

Drive the built CLI:

```bash
G=.mint/generated
PP="$PWD/$G/query-lexer/src:$PWD/$G/query-parser/src:$PWD/$G/odata-emitter/src:$PWD/$G/odq-cli/src"
PYTHONPATH="$PP" python -m odq_cli.cli "status = 'Active' and enddate < 2026-01-01"
# -> (status eq 'Active') and (enddate lt 2026-01-01)
```

### Re-recording after a spec change (manual, calls a real model)

```bash
MINT_LIVE=1 mint render <module> --range FR1:FR1   # one unit at a time, or
MINT_LIVE=1 mint live-smoke <module>               # force a full re-record
```

Default provider is `claude-cli` / `sonnet` (uses your Claude Code auth). New
cassettes are written under `resources/cassettes/` and should be committed.
