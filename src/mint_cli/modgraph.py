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
    on_stack_set: set[str] = set()
    path: list[str] = []
    # Explicit work stack of (module, entered) frames. ``entered`` is False on
    # first sight (pre-order: load spec, push dependencies) and True on the
    # second visit (post-order: emit the module). Iterative to avoid
    # RecursionError on deep chains, with set membership for O(1) cycle checks.
    work: list[tuple[str, bool]] = [(root_module, False)]

    while work:
        module, entered = work.pop()
        if entered:
            on_stack_set.discard(module)
            if path and path[-1] == module:
                path.pop()
            visited.add(module)
            order.append(module)
            continue
        if module in visited:
            continue
        if module in on_stack_set:
            index = path.index(module)
            cycle = " -> ".join(path[index:] + [module])
            raise MintError(
                f"Dependency cycle in module 'requires': {cycle}. "
                f"Fix: break the cycle in the specs' frontmatter."
            )
        on_stack_set.add(module)
        path.append(module)
        try:
            spec = load_spec(module)
        except MintError as exc:
            chain = " <- ".join(reversed(path))
            raise MintError(f"{exc} (required via: {chain})") from exc
        # Post-order frame first, then dependencies on top so they resolve before
        # this module is emitted. Preserve declared dependency order.
        work.append((module, True))
        for dependency in reversed(list(spec.requires)):
            work.append((dependency, False))

    return order


def direct_requires(load_spec: SpecLoader, module: str) -> list[str]:
    return list(load_spec(module).requires)
