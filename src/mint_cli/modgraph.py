"""Module dependency graph derived from spec ``requires`` edges.

``requires`` declares a build-ordering edge: a module can only be rendered after the
modules it requires. We resolve the transitive closure rooted at one module and
return a topological order (required modules first, the root last), detecting cycles
and missing specs with actionable errors.
"""

from __future__ import annotations

from typing import Callable

from .errors import MintError
from .specs import Spec

SpecLoader = Callable[[str], Spec]


def build_render_order(root_module: str, load_spec: SpecLoader) -> list[str]:
    """Return modules to render in dependency order, ending with ``root_module``.

    ``load_spec`` maps a module name to its parsed :class:`Spec` (and raises a
    MintError with a path hint when the spec file is missing).
    """
    order: list[str] = []
    visited: set[str] = set()
    on_stack: list[str] = []

    def visit(module: str) -> None:
        if module in visited:
            return
        if module in on_stack:
            cycle = " -> ".join(on_stack[on_stack.index(module):] + [module])
            raise MintError(
                f"Dependency cycle in module 'requires': {cycle}. "
                f"Fix: break the cycle in the specs' frontmatter."
            )
        on_stack.append(module)
        try:
            spec = load_spec(module)
        except MintError as exc:
            chain = " <- ".join(reversed(on_stack))
            raise MintError(f"{exc} (required via: {chain})") from exc
        for dependency in spec.requires:
            visit(dependency)
        on_stack.pop()
        visited.add(module)
        order.append(module)

    visit(root_module)
    return order


def direct_requires(load_spec: SpecLoader, module: str) -> list[str]:
    return list(load_spec(module).requires)
