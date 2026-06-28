"""Pluggable renderer adapters.

Public surface:
  * ``RenderRequest`` / ``RenderOutcome`` — the renderer contract types.
  * ``validate_patch`` / ``apply_patch`` — the file-patch contract.
  * ``get_renderer`` — factory that selects an adapter from config.
"""

from __future__ import annotations

import os
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
) -> Renderer:
    """Select a renderer adapter.

    ``model_client`` lets callers (notably tests) inject a mock client so the model
    path runs offline. When the provider is a model provider and no client is given,
    the default is replay from local cassettes. Set ``MINT_LIVE=1`` to wrap a live
    provider client in ``RecordingClient`` and refresh the cassettes.
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


def _default_model_client(
    *,
    provider: str,
    model: str,
    cassette_model: str,
    prompt_version: str,
    cassette_dir: Path | None,
) -> ModelClient:
    env_dir = os.environ.get("MINT_CASSETTE_DIR")
    root = Path(env_dir) if env_dir else (cassette_dir or Path("resources/cassettes"))
    if os.environ.get("MINT_LIVE") == "1":
        return RecordingClient(
            _live_model_client(provider=provider, model=model),
            cassette_dir=root,
            model=cassette_model,
            prompt_version=prompt_version,
        )
    return ReplayClient(cassette_dir=root, model=cassette_model, prompt_version=prompt_version)


def _live_model_client(*, provider: str, model: str) -> ModelClient:
    if provider in ANTHROPIC_LIVE_PROVIDERS:
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
