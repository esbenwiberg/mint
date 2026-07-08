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

## Render this graph

Cassettes are already recorded and committed under `resources/cassettes/`, so
rendering replays offline — no provider, no network:

```bash
cd examples/scrub
mint render scrub-cli      # replays the whole graph in dependency order
mint report scrub-cli
```

Drive the built CLI on fake data:

```bash
printf 'project,owner,owner_email,hourly_rate\n'\
'Apollo,Ada Lovelace,ada@example.test,150\n'\
'Zephyr,Ada Lovelace,ada@example.test,150\n' > export.csv
printf '{"seed":"demo-seed","columns":{"owner":"name","owner_email":"email","hourly_rate":"rate"}}' > config.json

G=.mint/generated
PP="$PWD/$G/export-parser/src:$PWD/$G/pseudonymizer/src:$PWD/$G/writer/src:$PWD/$G/scrub-cli/src"
PYTHONPATH="$PP" python -m scrub_cli.cli export.csv config.json
# Ada Lovelace maps to the same pseudonym in both rows; owner/email/rate are faked.
```

### Re-recording after a spec change (manual, calls a real model)

```bash
MINT_LIVE=1 mint render <module> --range FR1:FR1   # one unit at a time, or
MINT_LIVE=1 mint live-smoke <module>               # force a full re-record
```

Default provider is `claude-cli` / `sonnet` (uses your Claude Code auth). New
cassettes are written under `resources/cassettes/` and should be committed.
