"""Pluggable renderer adapters.

Public surface:
  * ``RenderRequest`` / ``RenderOutcome`` — the renderer contract types.
  * ``validate_patch`` / ``apply_patch`` — the file-patch contract.
  * ``get_renderer`` — factory that selects an adapter from config.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from ..errors import MintError
from .base import Renderer, RenderOutcome, RenderRequest
from .cassettes import RecordingClient, ReplayClient, cassette_id
from .deterministic import DeterministicRenderer
from .model import (
    AnthropicModelClient,
    ClaudeCliModelClient,
    CliModelClient,
    CodexCliModelClient,
    ModelClient,
    ModelOutputError,
    ModelRenderer,
    ScriptedModelClient,
    build_prompt,
    extract_json,
    normalize_feedback,
)
from .patch import apply_patch, validate_patch

__all__ = [
    "Renderer",
    "RenderRequest",
    "RenderOutcome",
    "DeterministicRenderer",
    "ModelRenderer",
    "ModelClient",
    "ModelOutputError",
    "ScriptedModelClient",
    "AnthropicModelClient",
    "CliModelClient",
    "ClaudeCliModelClient",
    "CodexCliModelClient",
    "RecordingClient",
    "ReplayClient",
    "cassette_id",
    "validate_patch",
    "apply_patch",
    "build_prompt",
    "extract_json",
    "normalize_feedback",
    "get_renderer",
    "cassette_model",
    "is_anthropic_live_provider",
    "is_model_provider",
    "valid_renderer_providers",
]

DETERMINISTIC_PROVIDERS = {"local", "deterministic"}
ANTHROPIC_LIVE_PROVIDERS = {"model", "anthropic"}
CLI_MODEL_PROVIDERS = {"claude-cli", "codex-cli"}
MODEL_PROVIDERS = ANTHROPIC_LIVE_PROVIDERS | CLI_MODEL_PROVIDERS
VALID_RENDERER_PROVIDERS = DETERMINISTIC_PROVIDERS | MODEL_PROVIDERS


def get_renderer(
    provider: str,
    *,
    model: str,
    prompt_version: str,
    model_client: ModelClient | None = None,
    cassette_dir: Path | None = None,
    max_response_chars: int = 200000,
    max_tokens: int | None = None,
) -> Renderer:
    """Select a renderer adapter.

    ``model_client`` lets callers (notably tests) inject a mock client so the model
    path runs offline. When the provider is a model provider and no client is given,
    the default is replay from local cassettes. Set ``MINT_LIVE=1`` (or ``true``/``on``)
    to wrap a live provider client in ``RecordingClient`` and refresh the cassettes.
    ``max_tokens`` is forwarded to the live Anthropic client when recording.
    """
    key = provider.strip().lower()
    if key in DETERMINISTIC_PROVIDERS:
        return DeterministicRenderer()
    if key in MODEL_PROVIDERS:
        scoped_model = cassette_model(key, model)
        client = model_client or _default_model_client(
            provider=key,
            model=model,
            cassette_model=scoped_model,
            prompt_version=prompt_version,
            cassette_dir=cassette_dir,
            max_tokens=max_tokens,
        )
        return ModelRenderer(
            client=client,
            prompt_version=prompt_version,
            max_response_chars=max_response_chars,
            model=scoped_model,
        )
    raise MintError(
        f"Unknown renderer provider '{provider}'. Use one of: "
        + ", ".join(valid_renderer_providers())
        + " (set it in mint.yaml under renderer.provider)."
    )


def _resolve_cassette_dir(cassette_dir: Path | None) -> Path:
    """Prefer an explicit ``cassette_dir`` arg over the ``MINT_CASSETTE_DIR`` env var.

    A stray env var silently overriding the caller's explicit path is how recordings
    end up where CI can't find them. Explicit wins; if the env disagrees, warn loudly
    rather than obey the env behind the caller's back.
    """
    env_dir = os.environ.get("MINT_CASSETTE_DIR")
    if cassette_dir is not None:
        if env_dir and Path(env_dir) != cassette_dir:
            warnings.warn(
                f"MINT_CASSETTE_DIR={env_dir!r} is ignored in favor of the explicit "
                f"cassette_dir={str(cassette_dir)!r}.",
                stacklevel=3,
            )
        return cassette_dir
    if env_dir:
        return Path(env_dir)
    return Path("resources/cassettes")


def _live_recording_enabled() -> bool:
    """Interpret ``MINT_LIVE``. Reject unrecognized truthy values loudly.

    Previously only the literal ``"1"`` enabled live recording, so ``MINT_LIVE=true``
    silently stayed in replay mode and surfaced later as a confusing "cassette not
    found". Accept the common truthy/falsey spellings; anything else is a hard error.
    """
    raw = os.environ.get("MINT_LIVE")
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"", "0", "false", "no", "off"}:
        return False
    raise MintError(
        f"MINT_LIVE={raw!r} is not recognized. Use 1/true/yes/on to live-record, or "
        "0/false/no/off (or leave it unset) to replay from cassettes."
    )


def _default_model_client(
    *,
    provider: str,
    model: str,
    cassette_model: str,
    prompt_version: str,
    cassette_dir: Path | None,
    max_tokens: int | None = None,
) -> ModelClient:
    root = _resolve_cassette_dir(cassette_dir)
    if _live_recording_enabled():
        return RecordingClient(
            _live_model_client(provider=provider, model=model, max_tokens=max_tokens),
            cassette_dir=root,
            model=cassette_model,
            prompt_version=prompt_version,
        )
    return ReplayClient(cassette_dir=root, model=cassette_model, prompt_version=prompt_version)


def _live_model_client(
    *, provider: str, model: str, max_tokens: int | None = None
) -> ModelClient:
    if provider in ANTHROPIC_LIVE_PROVIDERS:
        if max_tokens is not None:
            return AnthropicModelClient(model=model, max_tokens=max_tokens)
        return AnthropicModelClient(model=model)
    if provider == "claude-cli":
        return ClaudeCliModelClient(model=model)
    if provider == "codex-cli":
        return CodexCliModelClient(model=model)
    raise MintError(f"Renderer provider {provider!r} does not have a live model client.")


def cassette_model(provider: str, model: str) -> str:
    key = provider.strip().lower()
    return model if key in ANTHROPIC_LIVE_PROVIDERS else f"{key}:{model}"


def is_anthropic_live_provider(provider: str) -> bool:
    return provider.strip().lower() in ANTHROPIC_LIVE_PROVIDERS


def is_model_provider(provider: str) -> bool:
    return provider.strip().lower() in MODEL_PROVIDERS


def valid_renderer_providers() -> list[str]:
    return sorted(VALID_RENDERER_PROVIDERS)
