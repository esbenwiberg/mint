"""Validation and application of the JSON file-patch renderer contract."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import MintError

VALID_ACTIONS = {"write", "delete"}
VALID_ROOTS = {"module", "conformance"}


def validate_patch(patch: Any) -> dict[str, Any]:
    """Validate a raw patch object and return a normalized copy.

    Raises MintError with an actionable message on any structural problem so a
    misbehaving model renderer fails loudly instead of writing garbage.
    """
    if not isinstance(patch, dict):
        raise MintError(
            "Renderer patch must be a JSON object with a 'files' list "
            f"(got {type(patch).__name__})."
        )
    raw_files = patch.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise MintError("Renderer patch must contain a non-empty 'files' list.")

    files: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_files):
        files.append(_validate_file(entry, index))

    return {"summary": str(patch.get("summary", "")), "files": files}


def _validate_file(entry: Any, index: int) -> dict[str, Any]:
    where = f"files[{index}]"
    if not isinstance(entry, dict):
        raise MintError(f"Patch {where} must be an object.")

    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        raise MintError(f"Patch {where} is missing a non-empty 'path'.")

    action = str(entry.get("action", "write"))
    if action not in VALID_ACTIONS:
        raise MintError(
            f"Patch {where} has invalid action '{action}'. Use one of: "
            + ", ".join(sorted(VALID_ACTIONS))
        )

    root = str(entry.get("root", "module"))
    if root not in VALID_ROOTS:
        raise MintError(
            f"Patch {where} has invalid root '{root}'. Use one of: "
            + ", ".join(sorted(VALID_ROOTS))
        )

    _reject_escaping_path(path, where)

    normalized: dict[str, Any] = {"path": path, "action": action, "root": root}
    if action == "write":
        contents = entry.get("contents")
        if not isinstance(contents, str):
            raise MintError(f"Patch {where} action 'write' requires string 'contents'.")
        normalized["contents"] = contents
    return normalized


RESERVED_FIRST_COMPONENTS = {".git", ".mintgen"}


def _reject_escaping_path(path: str, where: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise MintError(f"Patch {where} path must be relative, not absolute: {path}")
    if not pure.parts:
        # Empty after normalization, e.g. "." or "" — nothing sane to write.
        raise MintError(f"Patch {where} path normalizes to an empty path: {path!r}")
    if ".." in pure.parts:
        raise MintError(f"Patch {where} path must not escape the target dir: {path}")
    # Block writes into the generated repo's VCS/metadata dirs: a patch entry like
    # ".git/hooks/pre-commit" would plant a hook that runs on the next checkpoint
    # commit, and ".mintgen" holds mint's own state. Reject at any depth.
    reserved = RESERVED_FIRST_COMPONENTS.intersection(pure.parts)
    if reserved:
        raise MintError(
            f"Patch {where} path must not write inside {sorted(reserved)[0]}/: {path}"
        )


def apply_patch(
    patch: dict[str, Any],
    module_dir: Path,
    conformance_dir: Path,
) -> list[str]:
    """Apply a validated patch. Returns the list of touched paths (for logging).

    Atomic-ish: every entry's root-containment is checked in a pre-pass before *any*
    file is written or deleted, so a single escaping entry can't leave a half-applied
    patch behind (the conformance dir is not git-managed, so a partial apply there is
    not recoverable via checkpoint rollback).
    """
    roots = {"module": module_dir, "conformance": conformance_dir}

    # Pre-pass: resolve and validate every target before touching disk. ``raw_target``
    # is the literal (unresolved) path — used for delete, so a symlink is removed as a
    # symlink rather than following it to its target. ``resolved`` is used for the
    # containment check and for writes (defense in depth against symlinked escapes).
    planned: list[tuple[dict[str, Any], Path, Path]] = []
    for entry in patch["files"]:
        base = roots[entry["root"]]
        base_resolved = base.resolve()
        raw_target = base / entry["path"]
        resolved = raw_target.resolve()
        if base_resolved not in resolved.parents:
            if resolved == base_resolved:
                raise MintError(
                    f"Patch path resolves to the {entry['root']} root itself: "
                    f"{entry['path']}"
                )
            raise MintError(f"Patch path escapes {entry['root']} dir: {entry['path']}")
        planned.append((entry, raw_target, resolved))

    # Apply pass: only reached once every entry has passed validation.
    touched: list[str] = []
    for entry, raw_target, resolved in planned:
        if entry["action"] == "write":
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(entry["contents"], encoding="utf-8")
            except OSError as exc:
                raise MintError(f"Failed to write {entry['path']}: {exc}") from exc
            touched.append(f"{entry['root']}:{entry['path']}")
        elif entry["action"] == "delete":
            _delete_target(raw_target, entry["path"])
            touched.append(f"{entry['root']}:{entry['path']} (deleted)")
    return touched


def _delete_target(target: Path, rel_path: str) -> None:
    # is_symlink() first, on the *unresolved* path: a broken symlink (dangling target)
    # reports exists()==False, so the old exists()-gated unlink silently reported it
    # deleted without removing it. Directories are refused rather than crashing with a
    # raw IsADirectoryError.
    if target.is_symlink():
        try:
            target.unlink()
        except OSError as exc:
            raise MintError(f"Failed to delete symlink {rel_path}: {exc}") from exc
        return
    if target.is_dir():
        raise MintError(
            f"Patch cannot delete directory {rel_path}; only files and symlinks are "
            "supported."
        )
    if target.exists():
        try:
            target.unlink()
        except OSError as exc:
            raise MintError(f"Failed to delete {rel_path}: {exc}") from exc
