from __future__ import annotations

from dataclasses import dataclass

import pytest

from mint_cli.errors import MintError
from mint_cli.modgraph import build_render_order


@dataclass
class FakeSpec:
    module: str
    requires: list[str]


def loader(graph: dict[str, list[str]]):
    def load(module: str) -> FakeSpec:
        if module not in graph:
            raise MintError(f"Spec file not found for {module}")
        return FakeSpec(module, graph[module])

    return load


def test_single_module():
    assert build_render_order("a", loader({"a": []})) == ["a"]


def test_linear_chain_orders_dependencies_first():
    graph = {"app": ["lib"], "lib": ["base"], "base": []}
    assert build_render_order("app", loader(graph)) == ["base", "lib", "app"]


def test_diamond_dedupes():
    graph = {"top": ["left", "right"], "left": ["base"], "right": ["base"], "base": []}
    order = build_render_order("top", loader(graph))
    assert order[-1] == "top"
    assert order.index("base") < order.index("left")
    assert order.index("base") < order.index("right")
    assert order.count("base") == 1


def test_cycle_detected():
    graph = {"a": ["b"], "b": ["a"]}
    with pytest.raises(MintError, match="Dependency cycle"):
        build_render_order("a", loader(graph))


def test_missing_required_spec_reports_chain():
    graph = {"app": ["missing"]}
    with pytest.raises(MintError, match="required via"):
        build_render_order("app", loader(graph))
