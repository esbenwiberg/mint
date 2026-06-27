# Model record/replay

The model renderer never calls the network by default. When `renderer.provider` is
`model` or `anthropic`, `mint` uses a `ReplayClient` unless `MINT_LIVE=1` is set.

## Cassette files

Cassettes live under:

```text
resources/cassettes/v1/<cassette-id>.json
```

The calc demo commits eleven replay cassettes: five for the clean graph render,
one for the edited-evaluator slice proof, and five for the edited-lexer cascade
proof. The self-hosting proof adds one cassette for `mint-hashing` FR1, so
`mint doctor` should report twelve replay cassettes in this repository.

The cassette id is a stable SHA-256 hash of:

- `promptVersion`
- module name
- functional unit id
- render phase
- attempt number
- full prompt text

That means prompt edits and prompt-version changes miss the old fixture loudly
instead of replaying a stale model response.

Each cassette stores:

- cassette format version
- pinned model id and prompt version
- request metadata and prompt hash
- full system prompt
- full user prompt
- raw model response

Replay validates all of those fields. A model, prompt-version, system-prompt, or
prompt-content mismatch is a hard error with a re-record hint.

## Replay mode

Replay is the default:

```bash
mint render calc-cli
```

If a cassette is missing or stale, the render fails with a command like:

```text
Fix: Re-record with: MINT_LIVE=1 mint live-smoke calc-cli.
```

This is the mode CI should use: no API key, no network, deterministic responses.

## Recording mode

Recording is explicit and environment-gated:

```bash
MINT_LIVE=1 mint live-smoke calc-cli
```

In this mode `RecordingClient` wraps `AnthropicModelClient`, writes each provider
response to `resources/cassettes/v1/`, and returns the live response to the render
loop. The `anthropic` package and `ANTHROPIC_API_KEY` are required only for this
live path.

## Attempt manifests

Every model render attempt records its cassette id in:

```text
generated/<module>/.mintgen/attempts/<unit>/<phase>-<attempt>.json
```

`mint report <module>` includes those ids so an offline run can be tied back to the
exact replay fixtures it used.
