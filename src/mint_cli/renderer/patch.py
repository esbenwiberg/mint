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


def _reject_escaping_path(path: str, where: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise MintError(f"Patch {where} path must be relative, not absolute: {path}")
    if ".." in pure.parts:
        raise MintError(f"Patch {where} path must not escape the target dir: {path}")


def apply_patch(
    patch: dict[str, Any],
    module_dir: Path,
    conformance_dir: Path,
) -> list[str]:
    """Apply a validated patch. Returns the list of touched paths (for logging)."""
    roots = {"module": module_dir, "conformance": conformance_dir}
    touched: list[str] = []
    for entry in patch["files"]:
        base = roots[entry["root"]]
        target = (base / entry["path"]).resolve()
        # Defense in depth: the resolved target must stay inside its root.
        base_resolved = base.resolve()
        if base_resolved not in target.parents and target != base_resolved:
            raise MintError(f"Patch path escapes {entry['root']} dir: {entry['path']}")

        if entry["action"] == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry["contents"], encoding="utf-8")
            touched.append(f"{entry['root']}:{entry['path']}")
        elif entry["action"] == "delete":
            if target.exists():
                target.unlink()
            touched.append(f"{entry['root']}:{entry['path']} (deleted)")
    return touched
