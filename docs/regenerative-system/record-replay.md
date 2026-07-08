# Model record/replay

The model renderer never calls a live provider by default. When
`renderer.provider` is `model`, `anthropic`, `claude-cli`, or `codex-cli`, `mint`
uses a `ReplayClient` unless `MINT_LIVE=1` is set.

## Cassette files

Cassettes live under:

```text
resources/cassettes/v1/<cassette-id>.json
```

The calc demo commits twelve replay cassettes: five for the clean graph render,
one for the edited-evaluator slice proof, five for the interface-changing
edited-lexer cascade proof, and one for the internal-only lexer edit that must
NOT cascade. The self-hosting proof adds one cassette for `mint-hashing` FR1, so
`mint doctor` should report thirteen replay cassettes in this repository.

These cassettes are generated, not hand-maintained. Any change to prompt
construction (system prompt, `build_prompt`, prompt hints, required-module
context) makes all of them stale; regenerate them from the committed fixture
responses and re-run the replay suites:

```bash
python scripts/regenerate_replay_cassettes.py
pytest tests/test_calc_graph.py tests/test_self_hosting.py -q
```

The canned responses live in `resources/replay-fixtures/responses.json`, keyed
`module/FRn` with `@variant` suffixes for the spec-edit scenarios. The script
mirrors the replay tests' scenarios exactly and asserts the cascade semantics
(interface change cascades, internal-only change does not) while recording.

The cassette id is a stable SHA-256 hash of:

- `promptVersion`
- module name
- functional unit id
- render phase
- attempt number
- full prompt text
- pinned model id (for cassettes recorded with a model; legacy model-less
  cassettes are still resolved as a fallback)

That means prompt edits and prompt-version changes miss the old fixture loudly
instead of replaying a stale model response.

Each cassette stores:

- cassette format version
- pinned model id and prompt version (`claude-cli` and `codex-cli` cassettes
  scope the model as `provider:model`)
- request metadata and prompt hash
- full system prompt
- full user prompt
- raw model response

Replay validates all of those fields. If the exact cassette filename key is absent
but another cassette has the same model, prompt version, system prompt, user prompt,
and request metadata, Mint treats it as the same replay fixture. A model,
prompt-version, system-prompt, or prompt-content mismatch is a hard error with a
re-record hint.

## Replay mode

Replay is the default:

```bash
mint render calc-cli
```

If a cassette is missing or stale, the render fails with a command like:

```text
Fix: Spec or prompt edits require live recording before offline render can replay.
Next: MINT_LIVE=1 mint render calc-cli to live-record the current render plan,
or MINT_LIVE=1 mint live-smoke calc-cli to force a full re-record.
```

This is the mode CI should use: no API key, no network, deterministic responses.

## Recording mode

Recording is explicit and environment-gated:

```bash
MINT_LIVE=1 mint render calc-cli
MINT_LIVE=1 mint live-smoke calc-cli
```

With `MINT_LIVE=1`, `mint render` records only the current incremental render plan.
`mint live-smoke` is the full forced re-record path and is the safer command when a
demo needs all cassettes refreshed. In both cases `RecordingClient` wraps the
selected live client, writes each provider response to `resources/cassettes/v1/`,
and returns the live response to the render loop. Anthropic API providers use
`AnthropicModelClient` and require the
`anthropic` package plus `ANTHROPIC_API_KEY`. CLI providers shell out to `claude`
or `codex` and use the auth already configured by those tools.

The default CLI commands are:

```bash
claude --print --output-format text --model <model>
codex exec --model <model> --sandbox read-only --ask-for-approval never --color never -
```

Override them with `MINT_CLAUDE_CLI_COMMAND` or `MINT_CODEX_CLI_COMMAND`. The
override command must read the render prompt from stdin and write the raw model
response to stdout.

## Attempt manifests

Every model render attempt records its cassette id in:

```text
.mint/generated/<module>/.mintgen/attempts/<unit>/<phase>-<attempt>.json
```

`mint report <module>` includes those ids so an offline run can be tied back to the
exact replay fixtures it used.
