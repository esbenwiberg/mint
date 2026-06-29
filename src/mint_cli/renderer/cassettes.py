"""Record/replay cassettes for model provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ..errors import MintError
from ..hashing import hash_json, hash_text
from ..state import now_iso
from .base import RenderRequest


class ModelClient(Protocol):
    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        ...

CASSETTE_VERSION = 1


def cassette_id(
    *,
    prompt_version: str,
    request: RenderRequest,
    prompt: str,
    model: str | None = None,
) -> str:
    """Stable request key for cassette filenames.

    The prompt text is part of the hash, so prompt edits miss the old cassette
    instead of silently replaying a stale response.
    """
    payload = {
        "promptVersion": prompt_version,
        "module": request.module,
        "unit": request.current_unit_id,
        "phase": request.phase,
        "attempt": request.attempt,
        "prompt": prompt,
    }
    if model is not None:
        payload["model"] = model
    return hash_json(payload)


def cassette_path(cassette_dir: Path, cassette_hash: str) -> Path:
    return cassette_dir / f"v{CASSETTE_VERSION}" / f"{cassette_hash}.json"


class RecordingClient:
    """Model client wrapper that records every provider response to disk."""

    def __init__(
        self,
        wrapped: ModelClient,
        *,
        cassette_dir: Path,
        model: str,
        prompt_version: str,
    ) -> None:
        self._wrapped = wrapped
        self._cassette_dir = cassette_dir
        self._model = model
        self._prompt_version = prompt_version

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        response = self._wrapped.complete(system=system, prompt=prompt, request=request)
        cassette_hash = cassette_id(
            prompt_version=self._prompt_version,
            request=request,
            prompt=prompt,
            model=self._model,
        )
        path = cassette_path(self._cassette_dir, cassette_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                _cassette_record(
                    cassette_hash,
                    model=self._model,
                    prompt_version=self._prompt_version,
                    system=system,
                    prompt=prompt,
                    response=response,
                    request=request,
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return response


class ReplayClient:
    """Offline model client that serves recorded provider responses."""

    def __init__(
        self,
        *,
        cassette_dir: Path,
        model: str,
        prompt_version: str,
    ) -> None:
        self._cassette_dir = cassette_dir
        self._model = model
        self._prompt_version = prompt_version

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        cassette_hash = cassette_id(
            prompt_version=self._prompt_version,
            request=request,
            prompt=prompt,
            model=self._model,
        )
        path = cassette_path(self._cassette_dir, cassette_hash)
        validation_hash = cassette_hash
        data: dict[str, Any] | None = None
        if not path.exists():
            legacy_hash = cassette_id(
                prompt_version=self._prompt_version,
                request=request,
                prompt=prompt,
            )
            legacy_path = cassette_path(self._cassette_dir, legacy_hash)
            if legacy_path.exists():
                path = legacy_path
                validation_hash = legacy_hash
        if not path.exists():
            equivalent = self._find_equivalent(system, prompt, request)
            if equivalent is not None:
                path = Path(str(equivalent["_path"]))
                validation_hash = str(equivalent["id"])
                data = equivalent
        if not path.exists():
            related = self._find_related(request)
            if related is not None:
                self._raise_related_mismatch(related, request, prompt)
            raise MintError(
                "Replay cassette not found for "
                f"{_request_label(request)} (id {cassette_hash}). "
                f"Fix: {_rerecord_hint(request.module)}"
            )

        if data is None:
            data = _load_cassette(path)
        self._validate(
            data,
            path=path,
            cassette_hash=validation_hash,
            system=system,
            prompt=prompt,
            request=request,
        )
        response = data.get("response")
        if not isinstance(response, str):
            raise MintError(f"Cassette {path} is missing a string response. Re-record it.")
        return response

    def _find_related(self, request: RenderRequest) -> dict[str, Any] | None:
        for path in sorted((self._cassette_dir / f"v{CASSETTE_VERSION}").glob("*.json")):
            try:
                data = _load_cassette(path)
            except MintError:
                continue
            raw_request = data.get("request")
            if not isinstance(raw_request, dict):
                continue
            if (
                raw_request.get("module") == request.module
                and raw_request.get("unit") == request.current_unit_id
                and raw_request.get("phase") == request.phase
                and raw_request.get("attempt") == request.attempt
            ):
                data["_path"] = path.as_posix()
                return data
        return None

    def _find_equivalent(
        self,
        system: str,
        prompt: str,
        request: RenderRequest,
    ) -> dict[str, Any] | None:
        expected_request = _request_metadata(request, prompt)
        for path in sorted((self._cassette_dir / f"v{CASSETTE_VERSION}").glob("*.json")):
            try:
                data = _load_cassette(path)
            except MintError:
                continue
            if not isinstance(data.get("id"), str):
                continue
            if data.get("model") != self._model:
                continue
            if data.get("promptVersion") != self._prompt_version:
                continue
            if data.get("system") != system or data.get("prompt") != prompt:
                continue
            if data.get("request") != expected_request:
                continue
            data["_path"] = path.as_posix()
            return data
        return None

    def _raise_related_mismatch(
        self,
        data: dict[str, Any],
        request: RenderRequest,
        prompt: str,
    ) -> None:
        path = str(data.get("_path", "matching cassette"))
        stored_prompt_version = data.get("promptVersion")
        stored_model = data.get("model")
        raw_request = data.get("request") if isinstance(data.get("request"), dict) else {}
        stored_prompt_hash = raw_request.get("promptHash") if isinstance(raw_request, dict) else None
        current_prompt_hash = hash_text(prompt)

        if stored_prompt_version != self._prompt_version:
            reason = (
                f"prompt_version mismatch: cassette has {stored_prompt_version!r}, "
                f"renderer asked for {self._prompt_version!r}"
            )
        elif stored_model != self._model:
            reason = (
                f"model mismatch: cassette has {stored_model!r}, "
                f"renderer asked for {self._model!r}"
            )
        elif stored_prompt_hash != current_prompt_hash:
            reason = "prompt content changed"
        else:
            reason = "cassette key changed"

        raise MintError(
            f"Replay cassette for {_request_label(request)} is stale ({reason}) at {path}. "
            f"Fix: {_rerecord_hint(request.module)}"
        )

    def _validate(
        self,
        data: dict[str, Any],
        *,
        path: Path,
        cassette_hash: str,
        system: str,
        prompt: str,
        request: RenderRequest,
    ) -> None:
        if data.get("cassetteVersion") != CASSETTE_VERSION:
            raise MintError(
                f"Cassette {path} has unsupported version {data.get('cassetteVersion')!r}; "
                "re-record it."
            )
        if data.get("id") != cassette_hash:
            raise MintError(f"Cassette {path} id does not match its request hash. Re-record it.")
        if data.get("promptVersion") != self._prompt_version:
            raise MintError(
                f"Cassette {path} prompt_version mismatch: has {data.get('promptVersion')!r}, "
                f"expected {self._prompt_version!r}. {_rerecord_hint(request.module)}"
            )
        if data.get("model") != self._model:
            raise MintError(
                f"Cassette {path} model mismatch: has {data.get('model')!r}, "
                f"expected {self._model!r}. {_rerecord_hint(request.module)}"
            )
        if data.get("system") != system:
            raise MintError(f"Cassette {path} system prompt changed. {_rerecord_hint(request.module)}")
        if data.get("prompt") != prompt:
            raise MintError(f"Cassette {path} prompt content changed. {_rerecord_hint(request.module)}")

        expected_request = _request_metadata(request, prompt)
        actual_request = data.get("request")
        if actual_request != expected_request:
            raise MintError(
                f"Cassette {path} request metadata mismatch. {_rerecord_hint(request.module)}"
            )


def _cassette_record(
    cassette_hash: str,
    *,
    model: str,
    prompt_version: str,
    system: str,
    prompt: str,
    response: str,
    request: RenderRequest,
) -> dict[str, Any]:
    return {
        "cassetteVersion": CASSETTE_VERSION,
        "id": cassette_hash,
        "createdAt": now_iso(),
        "model": model,
        "promptVersion": prompt_version,
        "request": _request_metadata(request, prompt),
        "system": system,
        "prompt": prompt,
        "response": response,
    }


def _request_metadata(request: RenderRequest, prompt: str) -> dict[str, Any]:
    return {
        "module": request.module,
        "unit": request.current_unit_id,
        "phase": request.phase,
        "attempt": request.attempt,
        "promptHash": hash_text(prompt),
    }


def _load_cassette(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MintError(f"Cassette {path} is invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MintError(f"Cassette {path} must contain a JSON object.")
    return data


def _request_label(request: RenderRequest) -> str:
    return (
        f"{request.module} {request.current_unit_id} "
        f"{request.phase} attempt {request.attempt}"
    )


def _rerecord_hint(module: str) -> str:
    return (
        "Spec or prompt edits require live recording before offline render can replay. "
        f"Next: MINT_LIVE=1 mint render {module} to live-record the current render plan, "
        f"or MINT_LIVE=1 mint live-smoke {module} to force a full re-record."
    )
