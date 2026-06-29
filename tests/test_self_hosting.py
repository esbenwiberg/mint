from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

from mint_cli import workflow
from mint_cli import hashing as handwritten


ROOT = Path(__file__).resolve().parents[1]


def make_self_host_project(make_project):
    project = make_project()
    shutil.copy(ROOT / ".mint" / "specs" / "mint-hashing.mint.md", project.spec_path("mint-hashing"))
    cassette_src = ROOT / "resources" / "cassettes"
    cassette_dst = project.root / "resources" / "cassettes"
    cassette_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cassette_src, cassette_dst)
    return project


def test_self_hosted_hashing_replays_and_matches_handwritten(make_project, monkeypatch, tmp_path):
    project = make_self_host_project(make_project)

    status, output = workflow.render_module("mint-hashing", root=project.root)

    assert status == 0, output
    meta = project.metadata("mint-hashing")
    assert meta["provider"] == "model"
    assert meta["model"] == "mint-replay-selfhost-v1"
    assert meta["functionalUnits"][0]["testQuality"]["status"] == "passed"

    monkeypatch.syspath_prepend(str(project.root / ".mint" / "generated" / "mint-hashing" / "src"))
    sys.modules.pop("mint_hashing", None)
    generated = importlib.import_module("mint_hashing")

    value = {"b": 2, "a": ["é"]}
    assert generated.canonical_json(value) == handwritten.canonical_json(value)
    assert generated.hash_json(value) == handwritten.hash_json(value)
    assert generated.hash_text("a\r\nb\n") == handwritten.hash_text("a\r\nb\n")
    assert generated.hash_text("a\r\nb\n") == generated.hash_text("a\nb\n")

    module = tmp_path / "module"
    (module / "src").mkdir(parents=True)
    (module / "src" / "code.py").write_text("print(1)\n", encoding="utf-8")
    (module / ".mintgen").mkdir()
    (module / ".mintgen" / "module.json").write_text("ignored", encoding="utf-8")
    (module / "__pycache__").mkdir()
    (module / "__pycache__" / "code.pyc").write_bytes(b"ignored")

    assert generated.should_skip(Path(".mintgen/module.json")) == handwritten.should_skip(
        Path(".mintgen/module.json")
    )
    assert generated.should_skip(Path("src/code.py")) == handwritten.should_skip(Path("src/code.py"))
    assert generated.hash_generated_files(module) == handwritten.hash_generated_files(module)
