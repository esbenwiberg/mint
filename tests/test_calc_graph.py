from __future__ import annotations

import json
import shutil
from pathlib import Path

from mint_cli import workflow
from mint_cli.specs import parse_spec_file


ROOT = Path(__file__).resolve().parents[1]
CALC_MODULES = ["lexer", "parser", "evaluator", "calc-cli"]


def make_calc_graph_project(make_project):
    project = make_project()
    for module in CALC_MODULES:
        shutil.copy(ROOT / ".mint" / "specs" / f"{module}.mint.md", project.spec_path(module))
    cassette_src = ROOT / "resources" / "cassettes"
    cassette_dst = project.root / "resources" / "cassettes"
    cassette_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cassette_src, cassette_dst)
    return project


def test_calc_graph_replays_from_clean_state(make_project):
    project = make_calc_graph_project(make_project)
    for module in CALC_MODULES:
        spec = parse_spec_file(project.spec_path(module))
        assert spec.template is None
        assert spec.renderer_provider == "model"

    status, output = workflow.render_module("calc-cli", root=project.root)

    assert status == 0, output
    assert output.index("RENDER lexer") < output.index("RENDER parser")
    assert output.index("RENDER parser") < output.index("RENDER evaluator")
    assert output.index("RENDER evaluator") < output.index("RENDER calc-cli")
    assert "- Renderer: model (model)" in output

    for module in CALC_MODULES:
        meta = project.metadata(module)
        assert meta["provider"] == "model"
        assert meta["model"] == "mint-replay-calc-v1"
        assert meta["promptVersion"] == "calc-v1"
        assert all(record["testQuality"]["status"] == "passed" for record in meta["functionalUnits"])

    report = json.loads(
        (
            project.root
            / ".mint" / "generated"
            / "calc-cli"
            / ".mintgen"
            / "reports"
            / "latest.json"
        ).read_text(encoding="utf-8")
    )
    assert report["provider"] == "model"
    assert report["units"][0]["cassetteIds"]


def test_evaluator_later_unit_edit_replays_only_that_slice(make_project):
    project = make_calc_graph_project(make_project)
    assert workflow.render_module("calc-cli", root=project.root)[0] == 0
    before = project.metadata("evaluator")
    fr1_commit = before["functionalUnits"][0]["finishedCommit"]
    fr2_commit = before["functionalUnits"][1]["finishedCommit"]

    spec_path = project.spec_path("evaluator")
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "`evaluate(\"missing(1)\")` raises `EvalError` with `unknown name` in the message.",
            "`evaluate(\"missing(1)\")` raises `EvalError` with `unknown name` in the clean message.",
        ),
        encoding="utf-8",
    )

    status, output = workflow.render_module("evaluator", root=project.root)

    assert status == 0, output
    assert "Range: FR2:FR2" in output
    assert "functional unit changed: FR2" in output
    assert "Completed FR1" not in output
    assert "Completed FR2" in output
    after = project.metadata("evaluator")
    assert after["functionalUnits"][0]["finishedCommit"] == fr1_commit
    assert after["functionalUnits"][1]["finishedCommit"] != fr2_commit


def test_reverting_spec_text_replays_original_cassette(make_project):
    project = make_calc_graph_project(make_project)
    assert workflow.render_module("calc-cli", root=project.root)[0] == 0

    spec_path = project.spec_path("evaluator")
    original = spec_path.read_text(encoding="utf-8")
    edited = original.replace(
        "`evaluate(\"missing(1)\")` raises `EvalError` with `unknown name` in the message.",
        "`evaluate(\"missing(1)\")` raises `EvalError` with `unknown name` in the clean message.",
    )
    spec_path.write_text(edited, encoding="utf-8")
    assert workflow.render_module("evaluator", root=project.root)[0] == 0

    spec_path.write_text(original, encoding="utf-8")
    status, output = workflow.render_module("evaluator", root=project.root)

    assert status == 0, output
    assert "functional unit changed: FR2" in output
    assert "Completed FR2" in output


def test_lexer_spec_edit_cascades_through_calc_graph(make_project):
    project = make_calc_graph_project(make_project)
    assert workflow.render_module("calc-cli", root=project.root)[0] == 0
    before = {
        module: project.metadata(module)["functionalUnits"][-1]["finishedCommit"]
        for module in CALC_MODULES
    }

    spec_path = project.spec_path("lexer")
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "Whitespace is ignored.",
            "Whitespace is ignored before and after every token.",
        ),
        encoding="utf-8",
    )

    status, output = workflow.render_module("calc-cli", root=project.root)

    assert status == 0, output
    assert "RENDER lexer" in output
    assert "functional unit changed: FR1" in output
    assert output.count("required module code changed") >= 3
    for module in CALC_MODULES:
        after = project.metadata(module)["functionalUnits"][-1]["finishedCommit"]
        assert after != before[module]
