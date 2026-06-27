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
    "RecordingClient",
    "ReplayClient",
    "cassette_id",
    "validate_patch",
    "apply_patch",
    "build_prompt",
    "extract_json",
    "get_renderer",
]

_DETERMINISTIC_PROVIDERS = {"local", "deterministic"}
_MODEL_PROVIDERS = {"model", "anthropic"}


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
    Anthropic client in ``RecordingClient`` and refresh the cassettes.
    """
    key = provider.strip().lower()
    if key in _DETERMINISTIC_PROVIDERS:
        return DeterministicRenderer()
    if key in _MODEL_PROVIDERS:
        client = model_client or _default_model_client(
            model=model,
            prompt_version=prompt_version,
            cassette_dir=cassette_dir,
        )
        return ModelRenderer(
            client=client,
            prompt_version=prompt_version,
            max_response_chars=max_response_chars,
        )
    raise MintError(
        f"Unknown renderer provider '{provider}'. Use one of: "
        + ", ".join(sorted(_DETERMINISTIC_PROVIDERS | _MODEL_PROVIDERS))
        + " (set it in mint.yaml under renderer.provider)."
    )


def _default_model_client(
    *,
    model: str,
    prompt_version: str,
    cassette_dir: Path | None,
) -> ModelClient:
    env_dir = os.environ.get("MINT_CASSETTE_DIR")
    root = Path(env_dir) if env_dir else (cassette_dir or Path("resources/cassettes"))
    if os.environ.get("MINT_LIVE") == "1":
        return RecordingClient(
            AnthropicModelClient(model=model),
            cassette_dir=root,
            model=model,
            prompt_version=prompt_version,
        )
    return ReplayClient(cassette_dir=root, model=model, prompt_version=prompt_version)
