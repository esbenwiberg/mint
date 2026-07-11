# Renderer contract

A renderer turns one functional unit into code. Every adapter implements the same
small interface and returns the same structured artifact, so the deterministic and
model-backed paths are fully interchangeable.

## Interface

```python
class Renderer(Protocol):
    name: str
    def render(self, request: RenderRequest) -> RenderOutcome: ...
```

### `RenderRequest` (`renderer/base.py`)

Everything known about the unit at render time:

- `module`, `stack`, `template` — identity and which deterministic template to use.
- `definitions`, `implementation`, `test` — the spec's non-functional sections.
- `imported_context` — shared definitions/impl/test pulled in via `imports`.
- `required_modules` — `[{module, files:[{path, contents}]}]` of already-generated
  required modules (so a model can call their APIs correctly).
- `units_so_far`, `current_unit` — the slice being built.
- `phase` (`"unit"` | `"conformance"`), `attempt`, `feedback` — retry context; on a
  retry, `feedback` carries the failing test output.
- `prompt_hints`, `code_fence_language` — target-stack guidance supplied by
  `stacks.py`, including TypeScript package-script expectations and code fences for
  required-module context. When the spec declares `styleLock`, the workflow appends
  the style-lock constraint to the hints so every prompt carries it.
- `unit_resources` — verbatim contents of the current unit's `resources:` files
  (`[{path, contents}]`); embedded in the prompt as authoritative inputs.

### `RenderOutcome`

- `patch` — the file patch (below).
- `renderer` — adapter name.
- `prompt`, `response` — raw model I/O (synthesized for the deterministic renderer
  so the audit trail is uniform).
- `classification`, `notes`.

## The file patch

```json
{
  "summary": "human-readable note",
  "files": [
    {"path": "src/pkg/x.py", "action": "write", "contents": "…", "root": "module"},
    {"path": "FR1/test_fr1.py", "action": "write", "contents": "…", "root": "conformance"},
    {"path": "tests/old.py", "action": "delete", "root": "module"}
  ]
}
```

- `action` ∈ `{write, delete}`; `write` requires `contents`.
- `root` ∈ `{module, conformance}` (default `module`). `module` targets the
  configured generated module directory (`.mint/generated/<module>/` by default);
  `conformance` targets `conformance/<module>/`.
- `path` must be **relative** and may not contain `..`. `validate_patch` rejects
  absolute paths and parent-escapes; `apply_patch` re-checks the resolved path stays
  inside its root (defense in depth against symlink tricks).

`validate_patch(raw)` → normalized patch or `MintError`. `apply_patch(patch,
module_dir, conformance_dir)` writes/deletes and returns the touched paths.

## Adapters

### Deterministic (`renderer/deterministic.py`) — default

Selects a template by the spec's `template` key (falling back to the module name)
and emits the file set for the current unit slice. Output depends only on the
request, so it is **repeatable and offline** — ideal for tests and CI. It ignores
`feedback` (a deterministic renderer cannot "try something different"), so a genuine
failure surfaces immediately. Unknown template → actionable `MintError`.

Templates live in `renderer/templates/`. Each generated module also gets a
`_mint_provenance.py` file derived from the spec text of every rendered unit — so a
meaningful spec change changes the generated **code**, which is what makes a required
module's code hash move and cascade to dependents (mirroring a real model renderer).

### Model-backed (`renderer/model.py`)

`ModelRenderer` builds a prompt from the request, calls a `ModelClient`, and parses
a JSON patch from the response (`extract_json` handles raw JSON, ```json fences, and
the first balanced object). The client is the only provider-specific piece:

```python
class ModelClient(Protocol):
    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str: ...
```

- `ScriptedModelClient` — offline mock for tests. Responses are a callable
  `(request) -> str` or a dict keyed `"<unit>:<phase>:<attempt>"` with
  `"<unit>:<phase>"`, `"<unit>"`, and `"default"` fallbacks.
- `ReplayClient` — default for model providers when no test client is injected.
  Serves `resources/cassettes/v1/<id>.json` without network, and fails loudly when
  prompt content, prompt version, system prompt, or model id no longer match.
- `RecordingClient` — wraps a live provider when `MINT_LIVE=1` and writes cassettes.
- `AnthropicModelClient` — real provider, imported lazily; errors clearly without
  `ANTHROPIC_API_KEY`. It is only reached through the explicit live-record path.
- `ClaudeCliModelClient` — shells out to `claude --print` with `--tools ""` (patch
  generation needs pure text output; an agent with file tools was the source of the
  stray-patch bug). Override with `MINT_CLAUDE_CLI_COMMAND`.
- `CodexCliModelClient` — shells out to `codex exec` in read-only/no-approval mode
  and reads the model response from stdout. Override with `MINT_CODEX_CLI_COMMAND`.

Every CLI client runs its command in an **empty scratch cwd**, never the project
directory: an agentic CLI that executes file tools despite being asked to print a
patch can only write into a throwaway directory, and no repo-level CLI config or
context leaks into the render.

See [record-replay.md](record-replay.md) for cassette layout and the re-record flow.

Model output is capped by `limits.maxModelResponseChars` (default `200000`). If a
model response is too large, unparseable, or parses to a patch that fails
`validate_patch`, the workflow writes a `patch_invalid` attempt manifest and feeds
the exact validation error back to the model while retries remain.

## Selecting an adapter

`get_renderer(provider, *, model, prompt_version, model_client=None, cassette_dir=None)`:

- `local` / `deterministic` → `DeterministicRenderer`.
- `model` / `anthropic` → `ModelRenderer`; uses the injected `model_client`, or
  `ReplayClient` by default. Set `MINT_LIVE=1` to wrap `AnthropicModelClient` in
  `RecordingClient` and refresh cassettes.
- `claude-cli` / `codex-cli` → `ModelRenderer`; uses `ReplayClient` by default.
  Set `MINT_LIVE=1` to wrap the matching CLI client in `RecordingClient`.
- anything else → `MintError` listing the valid providers.

Set the default in `mint.yaml` under `renderer.provider`. Tests inject a mock via
`render_module(..., model_client=...)`.
