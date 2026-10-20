"""Interface tracking: public stubs, literal warnings, and interface-change detection for dependents."""

from __future__ import annotations

import ast

from ..hashing import hash_json
from .context import ModuleContext, _dependent_modules


# --------------------------------------------------------------------------- #
# Public-interface analysis: cascade-cost warnings and change reporting
# --------------------------------------------------------------------------- #

# Above this size, a public literal is almost certainly content (a stylesheet, a
# template, a query) riding the dependent cascade rather than a genuine constant.
_PUBLIC_LITERAL_WARN_CHARS = 200


def _parse_python_stub(entry: dict[str, str]) -> ast.Module | None:
    if entry.get("language") != "python":
        return None
    try:
        return ast.parse(str(entry.get("contents", "")))
    except SyntaxError:
        return None


def public_literal_warnings(interface_files: list[dict[str, str]]) -> list[str]:
    """Warn about large string literals in a module's public interface stub.

    Everything in the stub rides ``requiredModuleCodeHash``: editing a fat public
    literal re-renders every dependent in full, usually for output that cannot
    differ. Python-only — other stacks have no literal-preserving stub.
    """
    warnings: list[str] = []
    for entry in interface_files:
        tree = _parse_python_stub(entry)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and len(value.value) > _PUBLIC_LITERAL_WARN_CHARS
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    warnings.append(
                        f"- WARN: public constant '{target.id}' in {entry.get('path')} "
                        f"carries a {len(value.value)}-char literal in the public "
                        "interface; every edit re-renders all dependents in full. "
                        "Consider a private literal behind a public accessor "
                        "(docs/spec-authoring.md, UI style lock)."
                    )
    return warnings


def interface_names(interface_files: list[dict[str, str]]) -> set[str]:
    """Top-level public names visible in a module's interface stub (Python only)."""
    names: set[str] = set()
    for entry in interface_files:
        tree = _parse_python_stub(entry)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


def interface_change_lines(
    context: ModuleContext,
    before: list[dict[str, str]],
    after: list[dict[str, str]],
) -> list[str]:
    if hash_json(before) == hash_json(after):
        return []
    added = sorted(interface_names(after) - interface_names(before))
    removed = sorted(interface_names(before) - interface_names(after))
    parts = []
    if added:
        parts.append("added: " + ", ".join(added))
    if removed:
        parts.append("removed: " + ", ".join(removed))
    detail = f" ({'; '.join(parts)})" if parts else ""
    lines = [f"- Public interface changed{detail}."]
    dependents = _dependent_modules(context)
    if dependents:
        lines.append(
            f"- Dependents that will re-render in full: {', '.join(dependents)}"
        )
    else:
        lines.append("- No dependent modules; no cascade.")
    return lines
