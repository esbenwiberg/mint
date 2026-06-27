"""Deterministic code templates used by the local renderer.

Each template is a callable ``build(request) -> list[patch-file]`` that returns the
file operations for the *current slice* of a module (every unit rendered so far)
plus the conformance test for the current unit. Templates are how the deterministic
renderer stays repeatable and fully offline: they encode known-good code for the
demo specs. The model renderer does not use templates — it works from spec text.
"""

from __future__ import annotations

from typing import Callable

from ..base import RenderRequest
from . import builtin

TemplateFn = Callable[[RenderRequest], list[dict]]

TEMPLATES: dict[str, TemplateFn] = {
    "taskstore": builtin.build_taskstore,
    "tasklist": builtin.build_tasklist,
    "example": builtin.build_example,
}


def get_template(name: str) -> TemplateFn | None:
    return TEMPLATES.get(name)


def known_templates() -> list[str]:
    return sorted(TEMPLATES)
