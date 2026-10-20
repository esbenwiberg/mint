"""Housekeeping commands: `mint clean`, `mint drift`, `mint prune`."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from ..config import (
    load_config,
)
from ..errors import MintError
from ..gitutil import (
    run_git,
)
from ..specs import parse_spec_file
from ..state import (
    load_metadata,
)
from ..stacks import PY_DEPS_DIRNAME
from .context import compute_context_hashes, load_context
from .planning import determine_render_plan
from .reports import _attempt_manifest_paths


def clean_module(module: str, yes: bool = False) -> tuple[int, str]:
    context = load_context(module)
    if not yes:
        return 1, "Refusing to clean without --yes. Re-run: mint clean " + module + " --yes\n"
    removed: list[str] = []
    for path in [context.generated_dir, context.conformance_dir]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(path.relative_to(context.root).as_posix())
    if not removed:
        return 0, f"CLEAN {module}\n- Nothing to remove.\n"
    return 0, f"CLEAN {module}\n" + "".join(f"- Removed {path}\n" for path in removed)


# Mint writes run reports (and other bookkeeping) into .mintgen/ after the final
# checkpoint commit, so that directory is expected to be dirty on a healthy render.
# Runtime caches appear whenever tests or the app run against the generated tree —
# mint scrubs them before every checkpoint (_remove_python_caches), so drift must
# not read them as hand edits either.
_DRIFT_PATHSPEC = ":(exclude).mintgen"

_DRIFT_IGNORED_DIRS = {
    ".mintgen",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "coverage",
    PY_DEPS_DIRNAME,
}


def _drift_ignored(path: str) -> bool:
    if path.endswith(".pyc"):
        return True
    return bool(_DRIFT_IGNORED_DIRS.intersection(path.strip("/").split("/")))


_DRIFT_STATUS_LABELS = {
    "??": "untracked",
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
}


def drift_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    """Show hand edits in a module's generated tree since its last checkpoint.

    Every successful unit render ends in a checkpoint commit in the module's
    nested git repo, so any uncommitted change outside ``.mintgen/`` is a hand
    edit (or a renderer stray). Exits 0 when clean and 1 when drift exists —
    like ``git diff --exit-code`` — so it can guard a re-record:
    ``mint drift m && MINT_LIVE=1 mint render m``.
    """
    context = load_context(module, root)
    if not (context.generated_dir / ".git").exists():
        return (
            1,
            f"FAIL drift {module}\n"
            f"- No generated output at {context.generated_dir.relative_to(context.root).as_posix()}\n"
            f"- Next: mint render {module}\n",
        )

    checkpoint = run_git(context.generated_dir, "log", "-1", "--format=%h %s").stdout.strip()
    status_out = run_git(context.generated_dir, "status", "--porcelain").stdout
    entries = []
    for entry in status_out.splitlines():
        code, _, path = entry.strip().partition(" ")
        if not _drift_ignored(path.strip()):
            entries.append((code, path.strip()))

    lines = [f"DRIFT {module}", f"- Checkpoint: {checkpoint}"]
    if not entries:
        lines.append("- Working tree matches the last checkpoint; no hand edits.")
        return 0, "\n".join(lines) + "\n"

    for code, path in entries:
        label = _DRIFT_STATUS_LABELS.get(code, code)
        lines.append(f"- Changed: {path} ({label})")
    lines.append(
        "- Hand edits do not survive a re-render: move what must stay into spec "
        "bullets (see docs/spec-authoring.md), then re-record."
    )
    diff = run_git(context.generated_dir, "diff", "HEAD", "--", _DRIFT_PATHSPEC).stdout
    if diff.strip():
        lines.append("")
        lines.append(diff.rstrip("\n"))
    return 1, "\n".join(lines) + "\n"


def prune_project(*, root: Path | None = None, yes: bool = False) -> tuple[int, str]:
    """Delete replay cassettes that no rendered module references.

    A cassette id is the hash of a full render prompt, and a unit's prompt embeds
    the generated code of earlier units plus runtime test feedback — so the live
    set cannot be computed from specs alone. It is only knowable from the attempt
    manifests of an up-to-date render. Prune therefore refuses while any module's
    generated output is stale or missing, then unions the manifests' cassette ids
    and removes every cassette file outside that set.
    """
    root = (root or Path.cwd()).resolve()
    config = load_config(root / "mint.yaml")
    spec_paths = sorted((root / config.specs_dir).glob("*.mint.md"))
    if not spec_paths:
        return 1, "FAIL prune\n- Project has no specs; nothing to derive cassette usage from.\n"

    failures: list[str] = []
    referenced: set[str] = set()
    verified = 0
    for spec_path in spec_paths:
        try:
            module = parse_spec_file(spec_path).module
            context = load_context(module, root)
        except MintError as exc:
            failures.append(str(exc))
            continue
        metadata = load_metadata(context.generated_dir)
        if metadata is None:
            failures.append(
                f"{module}: no generated output to derive cassette usage from "
                f"(fix: mint render {module})"
            )
            continue
        hashes = compute_context_hashes(context)
        plan = determine_render_plan(context, metadata, None, None, False, hashes)
        if not plan.noop:
            failures.append(
                f"{module}: generated output is not current — {plan.reason} "
                f"(fix: mint render {module})"
            )
            continue
        verified += 1
        for record in metadata.get("functionalUnits", []):
            if not isinstance(record, dict):
                continue
            attempts_dir = (
                context.generated_dir / ".mintgen" / "attempts" / str(record.get("id", ""))
            )
            if not attempts_dir.exists():
                continue
            for manifest_path in _attempt_manifest_paths(attempts_dir):
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                cassette = data.get("cassetteId") if isinstance(data, dict) else None
                if isinstance(cassette, str) and cassette:
                    referenced.add(cassette)

    if failures:
        lines = ["FAIL prune", *(f"- {failure}" for failure in failures)]
        lines.append(
            "- Prune deletes cassettes based on what current renders reference; "
            "it refuses while any module is stale."
        )
        return 1, "\n".join(lines) + "\n"

    cassette_root = root / "resources" / "cassettes"
    on_disk = sorted(cassette_root.rglob("*.json")) if cassette_root.exists() else []
    stems_on_disk = {path.stem for path in on_disk}
    orphans = [path for path in on_disk if path.stem not in referenced]
    missing = sorted(referenced - stems_on_disk)

    lines = [
        "PRUNE",
        f"- Modules verified current: {verified}",
        f"- Cassettes referenced: {len(referenced & stems_on_disk)}",
        f"- Cassettes on disk: {len(on_disk)}",
        f"- Orphans: {len(orphans)}",
    ]
    if missing:
        lines.append(
            f"- WARN: {len(missing)} referenced cassettes are missing from "
            "resources/cassettes; a fresh clone cannot replay until they are "
            "restored or re-recorded."
        )
    if not orphans:
        lines.append("- Nothing to prune.")
        return 0, "\n".join(lines) + "\n"
    for path in orphans:
        rel = path.relative_to(root).as_posix()
        if yes:
            path.unlink()
            lines.append(f"- Removed {rel}")
        else:
            lines.append(f"- Orphan {rel}")
    if not yes:
        lines.append("Refusing to delete without --yes. Re-run: mint prune --yes")
        return 1, "\n".join(lines) + "\n"
    return 0, "\n".join(lines) + "\n"
