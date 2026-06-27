---
module: mint-hashing
description: Self-hosted implementation of mint's hashing helpers
imports: []
requires: []
stack: python-lib
rendererProvider: model
rendererModel: mint-replay-selfhost-v1
rendererPromptVersion: selfhost-v1
---

## definitions

- Canonical JSON: JSON serialized with sorted keys, compact separators, and ASCII escaping.
- Generated file hash: a deterministic hash over generated files excluding runtime and mint metadata.

## implementation

- Use Python 3.12.
- Expose `canonical_json`, `hash_json`, `hash_text`, `hash_generated_files`, and `should_skip` from `src/mint_hashing/`.
- Match the behavior of `mint_cli.hashing`.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests compare observable behavior against known canonical values and skip rules.
- Include newline normalization and ignored generated-file paths.

## functional

- id: FR1
  title: Match mint hashing helper behavior
  spec:
    - `canonical_json(value)` returns sorted compact ASCII JSON.
    - `hash_json(value)` hashes canonical JSON with SHA-256.
    - `hash_text(value)` normalizes CRLF to LF before hashing.
    - `hash_generated_files(path)` ignores `.git`, `.mintgen`, caches, and `.pyc` files.
  acceptance:
    - `canonical_json({"b": 2, "a": ["é"]})` returns `{"a":["\\u00e9"],"b":2}`.
    - `hash_text("a\\r\\nb\\n") == hash_text("a\\nb\\n")`.
    - `hash_generated_files` changes when a tracked `.py` file changes and does not change when `.mintgen/module.json` or `__pycache__/x.pyc` changes.
