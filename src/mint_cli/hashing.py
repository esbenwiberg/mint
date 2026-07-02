from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def hash_generated_files(module_dir: Path) -> str:
    entries: list[dict[str, str]] = []
    if not module_dir.exists():
        return hash_json(entries)

    for path in sorted(module_dir.rglob("*")):
        if not path.is_file():
            continue
        if should_skip(path.relative_to(module_dir)):
            continue
        entries.append(
            {
                "path": path.relative_to(module_dir).as_posix(),
                "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return hash_json(entries)


def should_skip(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return False
    if parts[0] in {
        ".git",
        ".mintgen",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".vite",
        ".vitest",
        "coverage",
    }:
        return True
    if any(
        part in {"__pycache__", ".pytest_cache", "node_modules", ".vite", ".vitest", "coverage"}
        for part in parts
    ):
        return True
    return relative_path.suffix in {".pyc", ".pyo", ".tsbuildinfo"}
