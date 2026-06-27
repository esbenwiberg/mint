"""Renderer contract shared by every renderer adapter.

A renderer turns a :class:`RenderRequest` (everything known about one functional
unit at render time) into a :class:`RenderOutcome`. The outcome always carries the
parsed file *patch* plus the raw prompt/response so the workflow can persist a full
audit trail for every attempt, regardless of which adapter produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# A patch is the renderer contract: a list of file operations plus a summary.
# Each file op:
#   {"path": "src/pkg/x.py", "action": "write", "contents": "...", "root": "module"}
#   {"path": "tests/old.py", "action": "delete", "root": "module"}
# "root" is "module" (the generated module dir) or "conformance" (the black-box
# conformance dir for the current unit). It defaults to "module".


@dataclass(frozen=True)
class RenderRequest:
    """Everything a renderer needs to produce one functional unit."""

    module: str
    stack: str
    template: str | None
    spec_ir: dict[str, Any]
    definitions: list[dict[str, str]]
    implementation: list[str]
    test: list[str]
    imported_context: list[dict[str, Any]]
    required_modules: list[dict[str, Any]]
    units_so_far: list[dict[str, Any]]
    current_unit: dict[str, Any]
    phase: str = "unit"
    attempt: int = 1
    feedback: str | None = None

    @property
    def current_unit_id(self) -> str:
        return str(self.current_unit["id"])

    @property
    def rendered_unit_ids(self) -> set[str]:
        return {str(unit["id"]) for unit in self.units_so_far}


@dataclass(frozen=True)
class RenderOutcome:
    """Result of a single render attempt, with a full audit trail."""

    patch: dict[str, Any]
    renderer: str
    prompt: str | None = None
    response: str | None = None
    cassette_id: str | None = None
    classification: str = "rendered"
    notes: list[str] = field(default_factory=list)


class Renderer(Protocol):
    """Adapter interface. Implementations must be deterministic given a request
    *only* for the deterministic renderer; model renderers may vary."""

    name: str

    def render(self, request: RenderRequest) -> RenderOutcome:  # pragma: no cover - protocol
        ...
