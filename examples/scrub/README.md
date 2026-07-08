# scrub — deterministic project-data anonymizer

`scrub` reads a CSV export of project/resource data and pseudonymizes the PII
columns (names, emails, rates) using a **seeded** generator, so the same input
value always maps to the same fake value. That referential integrity holds
across rows and across files given the same seed.

```bash
scrub export.csv config.json > anonymized.csv
```

### Config file

```json
{
  "seed": "demo-seed",
  "columns": {
    "owner": "name",
    "owner_email": "email",
    "hourly_rate": "rate"
  }
}
```

- `seed` — any string; identical seed + input ⇒ identical output.
- `columns` — maps a column name to a PII type: `name`, `email`, or `rate`.
  Columns not listed are copied through unchanged.

## Module graph

```text
                 export-parser
                 /            \
        pseudonymizer        writer
                 \            /
                   scrub-cli
```

The task's suggested data flow is a straight pipeline
(`export-parser → pseudonymizer → writer → scrub-cli`). The `requires` graph
above encodes the **real code dependencies** instead: both `pseudonymizer` and
`writer` build on `export-parser`'s `Row` shape, and `scrub-cli` composes all
three. `scrub-cli`'s transitive build order is
`export-parser, pseudonymizer, writer, scrub-cli` — the pipeline order is
preserved at runtime while the dependency edges stay honest.

- **export-parser** — `parse_export(text)` → ordered rows; `ParseError` on
  ragged rows or a missing header.
- **pseudonymizer** — `Pseudonymizer(seed, mapping)` with deterministic,
  referentially-stable value replacement; `PseudonymizerError` on unknown types.
- **writer** — `write_export(header, rows)` → CSV text with correct quoting;
  `WriterError` on a row missing a header column.
- **scrub-cli** — the `scrub` command wiring config → parse → pseudonymize →
  write.

### Typed errors and exit codes

| Condition | Exit code |
|-----------|-----------|
| success | `0` |
| config error (missing/invalid config) | `2` |
| input / parse error (bad CSV) | `3` |
| writer error | `4` |

## No real personal data

Every fixture and example uses obviously fake values — names like `Ada`/`Nova`,
emails at the reserved `@example.test` domain, and a literal `demo-seed`. Do not
put real personal or customer data into specs, tests, or cassettes.

## Render this graph (one-time live record, then offline)

Template-free model specs — the first render records cassettes via a live
provider (`claude-cli` / `sonnet` by default). Run bottom-up from this directory:

```bash
cd examples/scrub

# 1. validate offline (already green)
for m in export-parser pseudonymizer writer scrub-cli; do mint lint "$m"; done

# 2. record cassettes live, bottom-up (manual: calls a real model)
MINT_LIVE=1 mint live-smoke export-parser
MINT_LIVE=1 mint live-smoke pseudonymizer
MINT_LIVE=1 mint live-smoke writer
MINT_LIVE=1 mint live-smoke scrub-cli

# 3. from now on, render/replay is offline
mint render scrub-cli
mint status scrub-cli
mint report scrub-cli
```

Recorded cassettes are written under `resources/cassettes/` and are meant to be
committed next to the specs.
