from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
from typing import Any

from .config import MintConfig
from .errors import MintError
from .hashing import hash_generated_files, hash_json
from .specs import FunctionalUnit, Spec


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_id(module: str) -> str:
    # Sub-second resolution + random suffix so two renders inside the same second
    # never collide on the same id (which would silently overwrite the earlier
    # render report).
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    return f"{stamp}-{secrets.token_hex(3)}-{module}"


def metadata_path(module_dir: Path) -> Path:
    return module_dir / ".mintgen" / "module.json"


def render_log_path(module_dir: Path) -> Path:
    return module_dir / ".mintgen" / "render.log"


def load_metadata(module_dir: Path) -> dict[str, Any] | None:
    path = metadata_path(module_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MintError(
            f"Generated metadata is corrupt JSON: {path} ({exc}). "
            f"Fix: run `mint clean {module_dir.name} --yes` and re-render, "
            "or restore the file from git."
        ) from exc


def write_metadata(module_dir: Path, metadata: dict[str, Any]) -> None:
    path = metadata_path(module_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: a crash mid-write must never leave a truncated module.json
    # that poisons every later command. Write to a temp file in the same dir
    # (so os.replace is a same-filesystem rename) then atomically swap it in.
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def append_render_log(module_dir: Path, line: str) -> None:
    path = render_log_path(module_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {line}\n")


def fresh_metadata(
    spec: Spec,
    config: MintConfig,
    module_dir: Path,
    *,
    imported_context_hash: str,
    required_module_code_hash: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "module": spec.module,
        "specPath": spec.path.relative_to(config.root).as_posix(),
        "renderId": render_id(spec.module),
        "specHash": spec.spec_hash,
        "nonFunctionalSpecHash": spec.non_functional_hash,
        "importedContextHash": imported_context_hash,
        "requiredModuleCodeHash": required_module_code_hash,
        "generatedCodeHash": hash_generated_files(module_dir),
        "lastSuccessfulUnitId": None,
        "provider": spec.renderer_provider or config.renderer.provider,
        "promptVersion": spec.renderer_prompt_version or config.renderer.prompt_version,
        "model": spec.renderer_model or config.renderer.model,
        "functionalUnits": [],
    }


def refresh_metadata_hashes(
    metadata: dict[str, Any],
    spec: Spec,
    module_dir: Path,
    *,
    imported_context_hash: str,
    required_module_code_hash: str,
) -> None:
    metadata["specHash"] = spec.spec_hash
    metadata["nonFunctionalSpecHash"] = spec.non_functional_hash
    metadata["importedContextHash"] = imported_context_hash
    metadata["requiredModuleCodeHash"] = required_module_code_hash
    metadata["generatedCodeHash"] = hash_generated_files(module_dir)


def unit_text_hash(unit: FunctionalUnit) -> str:
    return hash_json(
        {
            "id": unit.id,
            "title": unit.title,
            "spec": unit.spec,
            "acceptance": unit.acceptance,
            "resources": unit.resources,
        }
    )


def record_by_unit(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(record["id"]): record
        for record in metadata.get("functionalUnits", [])
        if isinstance(record, dict) and "id" in record
    }


def trim_records(metadata: dict[str, Any], keep_unit_ids: set[str]) -> None:
    metadata["functionalUnits"] = [
        record
        for record in metadata.get("functionalUnits", [])
        if record.get("id") in keep_unit_ids
    ]
    # lastSuccessfulUnitId must only ever point at a unit that actually passed;
    # the last *kept* record may be a failed/incomplete unit.
    passed = [
        record
        for record in metadata["functionalUnits"]
        if record.get("status") == "passed"
    ]
    metadata["lastSuccessfulUnitId"] = passed[-1]["id"] if passed else None


def write_attempt(
    module_dir: Path,
    unit_id: str,
    phase: str,
    attempt: int,
    *,
    script: str | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    classification: str = "",
    summary: str = "",
    prompt: str | None = None,
    response: str | None = None,
    patch: Any | None = None,
    renderer: str | None = None,
    cassette_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist a full audit trail for one attempt.

    Every render/test attempt writes its prompt, raw response, parsed patch, and
    captured stdout/stderr to ``.mintgen/attempts/<unit>/`` plus a JSON manifest
    that records the classification and references each artifact.
    """
    attempt_dir = module_dir / ".mintgen" / "attempts" / unit_id
    attempt_dir.mkdir(parents=True, exist_ok=True)
    base = f"{phase}-{attempt}"

    def _rel(path: Path) -> str:
        return path.relative_to(module_dir).as_posix()

    record: dict[str, Any] = {
        "unitId": unit_id,
        "phase": phase,
        "attempt": attempt,
        "renderer": renderer,
        "script": script,
        "exitCode": exit_code,
        "classification": classification,
        "summary": summary,
        "cassetteId": cassette_id,
        "promptPath": None,
        "responsePath": None,
        "patchPath": None,
        "stdoutPath": None,
        "stderrPath": None,
    }
    if extra:
        record.update(extra)

    if prompt is not None:
        path = attempt_dir / f"{base}.prompt.txt"
        path.write_text(prompt, encoding="utf-8")
        record["promptPath"] = _rel(path)
    if response is not None:
        path = attempt_dir / f"{base}.response.txt"
        path.write_text(response, encoding="utf-8")
        record["responsePath"] = _rel(path)
    if patch is not None:
        path = attempt_dir / f"{base}.patch.json"
        path.write_text(json.dumps(patch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record["patchPath"] = _rel(path)
    if script is not None or stdout or stderr:
        stdout_path = attempt_dir / f"{base}.stdout.log"
        stderr_path = attempt_dir / f"{base}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        record["stdoutPath"] = _rel(stdout_path)
        record["stderrPath"] = _rel(stderr_path)

    json_path = attempt_dir / f"{base}.json"
    json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return json_path
