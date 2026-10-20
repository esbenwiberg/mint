"""Spec linting: vague wording, missing testable assertions, threshold boundary checks."""

from __future__ import annotations

from pathlib import Path
import re

from ..errors import MintError
from ..specs import FunctionalUnit, parse_spec_file
from .context import configured_specs_dir


def lint_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    path = root / configured_specs_dir(root) / f"{module}.mint.md"
    failures: list[str] = []
    warnings: list[str] = []

    try:
        spec = parse_spec_file(path)
    except MintError as exc:
        return 1, f"FAIL lint {module}\n- {exc}\n"

    for unit in spec.functional_units:
        acceptance_text = " ".join(unit.acceptance)
        if _looks_vague(acceptance_text):
            failures.append(
                f"{unit.id} acceptance is vague; name the observable output, state, or error."
            )
        if not _has_testable_assertion(acceptance_text):
            failures.append(
                f"{unit.id} acceptance has no testable assertion; add a concrete return, output, "
                "exit code, stored state, or error condition."
            )
        failures.extend(_threshold_boundary_failures(unit))

    combined = " ".join(
        spec.implementation
        + spec.test
        + [item for unit in spec.functional_units for item in unit.spec + unit.acceptance]
    ).lower()
    if not any(term in combined for term in _EDGE_HINT_TERMS):
        warnings.append(
            "No edge-case coverage hint found; consider empty, invalid, missing, duplicate, "
            "unknown, or error cases."
        )

    status = "FAIL" if failures else "PASS"
    lines = [
        f"{status} lint {module}",
        f"- Parsed {path.relative_to(root).as_posix()}",
        f"- Functional units: {len(spec.functional_units)}",
    ]
    lines.extend(f"- FAIL: {failure}" for failure in failures)
    lines.extend(f"- WARN: {warning}" for warning in warnings)
    if not failures and not warnings:
        lines.append("- Spec quality checks passed.")
    return (1 if failures else 0), "\n".join(lines) + "\n"


_VAGUE_TERMS = {
    "works",
    "properly",
    "correctly",
    "as needed",
    "etc",
    "todo",
    "tbd",
    "nice",
}

_TESTABLE_TERMS = {
    "return",
    "returns",
    "show",
    "shows",
    "print",
    "prints",
    "raise",
    "raises",
    "exit",
    "exits",
    "store",
    "stores",
    "write",
    "writes",
    "reject",
    "rejects",
    "accept",
    "accepts",
    "fail",
    "fails",
    "pass",
    "passes",
    "contain",
    "contains",
    "surface",
    "surfaces",
    "emit",
    "emits",
    "equal",
    "equals",
    "before",
    "after",
}

_EDGE_HINT_TERMS = {
    "empty",
    "invalid",
    "missing",
    "unknown",
    "duplicate",
    "zero",
    "edge",
    "error",
    "fail",
    "reject",
    "no ",
}


def _looks_vague(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _VAGUE_TERMS)


def _has_testable_assertion(text: str) -> bool:
    lower = text.lower()
    if any(symbol in text for symbol in ["==", "!=", "<=", ">=", "→", "->", " exit ", "$?"]):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _TESTABLE_TERMS)


_SYMBOLIC_THRESHOLD_RE = re.compile(
    r"(?:<=|>=|<|>|≤|≥)\s*(?P<right>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?)"
    r"|(?P<left>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?)\s*(?:<=|>=|<|>|≤|≥)",
    re.IGNORECASE,
)
_WORD_THRESHOLD_RE = re.compile(
    r"\b(?:less than|greater than|at least|at most|no more than|no less than|"
    r"below|above|under|over)\s+"
    r"(?P<number>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?)\b",
    re.IGNORECASE,
)


def _threshold_boundary_failures(unit: FunctionalUnit) -> list[str]:
    thresholds: dict[str, str] = {}
    for item in unit.spec:
        for number in _relational_thresholds(item):
            thresholds.setdefault(number, item)
    if not thresholds:
        return []

    acceptance_text = " ".join(unit.acceptance)
    failures: list[str] = []
    for number, source in sorted(thresholds.items()):
        if re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", acceptance_text):
            continue
        failures.append(
            f"{unit.id} relational threshold {number} needs an exact-boundary "
            f"acceptance example; found in spec: {source}"
        )
    return failures


def _relational_thresholds(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in _SYMBOLIC_THRESHOLD_RE.finditer(text):
        number = match.group("right") or match.group("left")
        if number is not None:
            numbers.add(number)
    for match in _WORD_THRESHOLD_RE.finditer(text):
        numbers.add(match.group("number"))
    return numbers
