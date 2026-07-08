from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from mint_cli import stacks, test_quality, workflow
from mint_cli.errors import MintError
from mint_cli.renderer import ScriptedModelClient


# --------------------------------------------------------------------------- #
# deterministic, multi-module lifecycle
# --------------------------------------------------------------------------- #


def test_init_project_prints_skeleton_without_writing(tmp_path):
    status, output = workflow.init_project(root=tmp_path)

    assert status == 0
    assert "mint Phase 0 skeleton" in output
    assert "mint init --write" in output
    assert not (tmp_path / "mint.yaml").exists()
    assert not (tmp_path / "test_scripts").exists()


def test_init_project_write_scaffolds_project(tmp_path):
    status, output = workflow.init_project(write=True, root=tmp_path)

    assert status == 0, output
    assert "INIT mint project" in output
    assert "First smoke test: mint render example" in output
    assert "New module: choose a module slug and model id" in output
    for rel in [".mint/specs", "resources", ".mint/generated", "conformance", "test_scripts"]:
        assert (tmp_path / rel).is_dir()
    for rel in ["resources/.gitkeep", ".mint/generated/.gitkeep", "conformance/.gitkeep"]:
        assert (tmp_path / rel).is_file()
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".mint/generated/*" in gitignore
    assert "!.mint/generated/.gitkeep" in gitignore
    assert "conformance/*" in gitignore
    assert (tmp_path / ".mint" / "specs" / "example.mint.md").is_file()
    config = workflow.load_config(tmp_path / "mint.yaml")
    assert config.default_stack == "python-cli"
    assert config.specs_dir == ".mint/specs"
    assert config.renderer.provider == "local"
    for rel in [
        "test_scripts/prepare_environment.sh",
        "test_scripts/run_unit_tests.sh",
        "test_scripts/run_conformance_tests.sh",
    ]:
        script = tmp_path / rel
        assert script.is_file()
        assert os.access(script, os.X_OK)
    doctor_status, doctor_output = workflow.doctor_project(root=tmp_path)
    assert doctor_status == 0, doctor_output
    health_status, health_output = workflow.healthcheck_module("example", root=tmp_path)
    assert health_status == 0, health_output


def test_init_project_write_uses_existing_configured_output_dirs(tmp_path):
    (tmp_path / "mint.yaml").write_text(
        workflow.DEFAULT_MINT_YAML.replace(
            "generatedDir: .mint/generated",
            "generatedDir: mint-generated",
        ).replace(
            "conformanceDir: conformance",
            "conformanceDir: mint-conformance",
        ),
        encoding="utf-8",
    )

    status, output = workflow.init_project(write=True, root=tmp_path)

    assert status == 0, output
    assert (tmp_path / "mint-generated").is_dir()
    assert (tmp_path / "mint-generated" / ".gitkeep").is_file()
    assert (tmp_path / "mint-conformance").is_dir()
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "mint-generated/*" in gitignore
    assert "!mint-generated/.gitkeep" in gitignore
    assert "mint-conformance/*" in gitignore
    assert not (tmp_path / "generated").exists()


def test_init_project_write_is_idempotent_and_preserves_files(tmp_path):
    custom_config = tmp_path / "mint.yaml"
    custom_config.write_text("# custom config\n", encoding="utf-8")
    custom_spec = tmp_path / ".mint" / "specs" / "example.mint.md"
    custom_spec.parent.mkdir(parents=True)
    custom_spec.write_text("# custom spec\n", encoding="utf-8")

    first_status, first_output = workflow.init_project(write=True, root=tmp_path)
    second_status, second_output = workflow.init_project(write=True, root=tmp_path)

    assert first_status == 0, first_output
    assert second_status == 0, second_output
    assert custom_config.read_text(encoding="utf-8") == "# custom config\n"
    assert custom_spec.read_text(encoding="utf-8") == "# custom spec\n"
    assert "Kept existing mint.yaml" in first_output
    assert "Kept existing .mint/specs/example.mint.md" in first_output
    assert "Kept existing test_scripts/run_unit_tests.sh" in second_output
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count(".mint/generated/*") == 1


def test_render_top_module_renders_requirements_first(demo_project, monkeypatch):
    monkeypatch.chdir(demo_project.root)
    status, output = workflow.render_module("tasklist")
    assert status == 0, output
    # taskstore (required) appears before tasklist in the output.
    assert output.index("RENDER taskstore") < output.index("RENDER tasklist")
    assert "Completed FR1" in output and "Completed FR2" in output

    # Both modules produced their own nested git repo + metadata.
    for module in ("taskstore", "tasklist"):
        meta = demo_project.metadata(module)
        assert meta["lastSuccessfulUnitId"] == "FR2"
        assert (demo_project.root / ".mint" / "generated" / module / ".git").is_dir()
        assert meta["functionalUnits"][0]["finishedCommit"]


def test_second_render_is_noop(rendered_demo_project, monkeypatch):
    # Runs against a copied snapshot, which doubles as proof that content
    # hashes are location-independent: a moved checkout must still no-op.
    monkeypatch.chdir(rendered_demo_project.root)
    status, output = workflow.render_module("tasklist")
    assert status == 0
    assert "NOOP taskstore" in output and "NOOP tasklist" in output


def test_render_existing_generated_dir_without_metadata_requires_force(demo_project, monkeypatch):
    monkeypatch.chdir(demo_project.root)
    unmanaged = demo_project.root / ".mint" / "generated" / "taskstore"
    unmanaged.mkdir(parents=True)
    (unmanaged / "copied.py").write_text("# from another checkout\n", encoding="utf-8")

    status, output = workflow.render_module("taskstore")

    assert status == 1
    assert "Generated repo is missing metadata" in output
    assert "Next: mint render taskstore --force" in output


def test_render_force_replaces_generated_dir_without_metadata(demo_project, monkeypatch):
    monkeypatch.chdir(demo_project.root)
    unmanaged = demo_project.root / ".mint" / "generated" / "taskstore"
    unmanaged.mkdir(parents=True)
    (unmanaged / "copied.py").write_text("# from another checkout\n", encoding="utf-8")

    status, output = workflow.render_module("taskstore", force=True)

    assert status == 0, output
    assert "RENDER taskstore" in output
    assert not (unmanaged / "copied.py").exists()
    assert demo_project.metadata("taskstore")["lastSuccessfulUnitId"] == "FR2"


def test_metadata_records_hashes_attempts_commits(rendered_demo_project):
    demo_project = rendered_demo_project
    meta = demo_project.metadata("tasklist")
    assert meta["specHash"] and meta["nonFunctionalSpecHash"]
    assert meta["importedContextHash"] and meta["requiredModuleCodeHash"]
    assert meta["provider"] == "local"
    fr1 = meta["functionalUnits"][0]
    assert fr1["attempts"] == {
        "implementation": 1,
        "unit": 1,
        "conformance": 1,
        "testQuality": 1,
    }
    assert fr1["beforeCommit"] and fr1["finishedCommit"]
    assert fr1["renderer"] == "deterministic"
    assert fr1["testQuality"]["status"] == "passed"
    quality_attempt = (
        demo_project.root
        / ".mint" / "generated"
        / "tasklist"
        / ".mintgen"
        / "attempts"
        / "FR1"
        / "test-quality-1.json"
    )
    quality_data = json.loads(quality_attempt.read_text())
    assert quality_data["classification"] == "passed"
    assert quality_data["testQuality"]["coverage"]["status"] == "skipped"
    assert "deferred until final functional unit" in quality_data["testQuality"]["coverage"]["reason"]
    fr2 = meta["functionalUnits"][1]
    assert fr2["testQuality"]["coverage"]["percent"] >= 60


def test_editing_later_unit_rerenders_only_that_slice(rendered_demo_project, monkeypatch):
    demo_project = rendered_demo_project
    monkeypatch.chdir(demo_project.root)
    before = demo_project.metadata("tasklist")
    fr1_commit = before["functionalUnits"][0]["finishedCommit"]
    fr2_commit = before["functionalUnits"][1]["finishedCommit"]

    # Edit FR2 spec text only.
    spec = demo_project.spec_path("tasklist")
    spec.write_text(
        spec.read_text().replace(
            "Tasks appear in the order they were added.",
            "Tasks appear in the exact order they were added.",
        ),
        encoding="utf-8",
    )

    status, output = workflow.render_module("tasklist")
    assert status == 0, output
    assert "Range: FR2:FR2" in output
    assert "functional unit changed: FR2" in output

    after = demo_project.metadata("tasklist")
    # FR1 checkpoint preserved, FR2 re-rendered (new commit).
    assert after["functionalUnits"][0]["finishedCommit"] == fr1_commit
    assert after["functionalUnits"][1]["finishedCommit"] != fr2_commit


def test_editing_required_module_cascades_to_dependent(rendered_demo_project, monkeypatch):
    demo_project = rendered_demo_project
    monkeypatch.chdir(demo_project.root)
    before = demo_project.metadata("tasklist")["requiredModuleCodeHash"]

    spec = demo_project.spec_path("taskstore")
    spec.write_text(
        spec.read_text().replace(
            "`TaskStore.list_tasks()` returns all stored Tasks.",
            "`TaskStore.list_tasks()` returns all stored Tasks in a list.",
        ),
        encoding="utf-8",
    )

    status, output = workflow.render_module("tasklist")
    assert status == 0, output
    # taskstore re-renders the changed unit, then tasklist re-renders because the
    # required module's generated code hash moved.
    assert "RENDER taskstore" in output
    assert "required module code changed" in output
    after = demo_project.metadata("tasklist")["requiredModuleCodeHash"]
    assert after != before


def test_force_rerenders_all(rendered_demo_project, monkeypatch):
    monkeypatch.chdir(rendered_demo_project.root)
    status, output = workflow.render_module("taskstore", force=True)
    assert status == 0
    assert "forced render" in output
    assert "Range: FR1:FR2" in output


def test_range_renders_subset(rendered_demo_project, monkeypatch):
    monkeypatch.chdir(rendered_demo_project.root)
    status, output = workflow.render_module("taskstore", unit_range="FR2:FR2")
    assert status == 0
    assert "explicit range" in output
    assert "Range: FR2:FR2" in output


def test_prior_conformance_runs_as_regression(rendered_demo_project):
    demo_project = rendered_demo_project
    # The conformance attempt for FR2 must have collected FR1 + FR2 (regression).
    attempt = (
        demo_project.root
        / ".mint" / "generated"
        / "taskstore"
        / ".mintgen"
        / "attempts"
        / "FR2"
        / "conformance-1.json"
    )
    data = json.loads(attempt.read_text())
    stdout = (demo_project.root / ".mint" / "generated" / "taskstore" / data["stdoutPath"]).read_text()
    assert "2 passed" in stdout


def test_caches_kept_out_of_checkpoint(rendered_demo_project):
    gen = rendered_demo_project.root / ".mint" / "generated" / "taskstore"
    assert not list(gen.rglob("__pycache__"))
    assert not list(gen.rglob("*.pyc"))


# --------------------------------------------------------------------------- #
# clean / inspect / status / parse / healthcheck
# --------------------------------------------------------------------------- #


def test_clean_requires_yes(rendered_demo_project, monkeypatch):
    demo_project = rendered_demo_project
    monkeypatch.chdir(demo_project.root)
    status, output = workflow.clean_module("taskstore", yes=False)
    assert status == 1 and "--yes" in output
    assert (demo_project.root / ".mint" / "generated" / "taskstore").exists()

    status, output = workflow.clean_module("taskstore", yes=True)
    assert status == 0 and "Removed" in output
    assert not (demo_project.root / ".mint" / "generated" / "taskstore").exists()


def test_inspect_shows_record_and_attempts(rendered_demo_project, monkeypatch):
    monkeypatch.chdir(rendered_demo_project.root)
    status, output = workflow.inspect_unit("taskstore", "FR1")
    assert status == 0
    assert "Unit: FR1" in output
    assert "status: passed" in output
    assert "unit attempt=1" in output and "conformance attempt=1" in output


def test_status_suggests_render_when_changed(rendered_demo_project, monkeypatch):
    demo_project = rendered_demo_project
    monkeypatch.chdir(demo_project.root)
    out = workflow.status_module("taskstore")
    assert "Suggested render: no-op" in out

    spec = demo_project.spec_path("taskstore")
    spec.write_text(spec.read_text().replace("Buy milk", "Buy bread"), encoding="utf-8")
    out = workflow.status_module("taskstore")
    assert "--from FR1" in out


def test_parse_emits_ir_with_imports_requires(demo_project, monkeypatch):
    monkeypatch.chdir(demo_project.root)
    ir = json.loads(workflow.parse_module("tasklist"))
    assert ir["module"] == "tasklist"
    assert ir["requires"] == ["taskstore"]
    assert ir["imports"] == ["taskstore"]


def test_healthcheck_flags_non_executable_script(demo_project, monkeypatch):
    monkeypatch.chdir(demo_project.root)
    (demo_project.root / "test_scripts" / "run_unit_tests.sh").chmod(0o644)
    status, output = workflow.healthcheck_module("taskstore")
    assert status == 1
    assert "not executable" in output and "chmod +x" in output


def test_healthcheck_guides_missing_default_script(demo_project):
    (demo_project.root / "test_scripts" / "run_unit_tests.sh").unlink()

    status, output = workflow.healthcheck_module("taskstore", root=demo_project.root)

    assert status == 1
    assert "Unit script missing: test_scripts/run_unit_tests.sh" in output
    assert "mint init --write" in output


def test_render_reports_missing_script_without_traceback(demo_project):
    config = demo_project.root / "mint.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "unit: test_scripts/run_unit_tests.sh",
            "unit: test_scripts/missing.sh",
        ),
        encoding="utf-8",
    )

    status, output = workflow.render_module("taskstore", root=demo_project.root)

    assert status == 1
    assert "FAIL taskstore" in output
    assert "Unit script missing: test_scripts/missing.sh" in output
    assert "mint init --write" in output


def test_healthcheck_flags_missing_required_spec(make_project, monkeypatch):
    project = make_project()
    project.write_spec(
        "lonely",
        """---
module: lonely
description: needs a missing module
imports: []
requires: [ghost]
stack: python-lib
template: taskstore
---

## definitions
- X: y.
## implementation
- Use Python 3.12.
## test
- pytest.
## functional
- id: FR1
  title: t
  spec:
    - s.
  acceptance:
    - a.
""",
    )
    monkeypatch.chdir(project.root)
    status, output = workflow.healthcheck_module("lonely")
    assert status == 1
    assert "ghost" in output


def test_new_module_scaffolds_parseable_spec(make_project):
    project = make_project()
    status, output = workflow.new_module("calc-cli", requires=["taskstore"], root=project.root)

    assert status == 0, output
    assert ".mint/specs/calc-cli.mint.md" in output
    assert "Before rendering, add a deterministic template or set rendererProvider: model" in output
    text = project.spec_path("calc-cli").read_text(encoding="utf-8")
    assert "requires: [taskstore]" in text
    spec = workflow.parse_spec_file(project.spec_path("calc-cli"))
    assert spec.module == "calc-cli"
    assert spec.requires == ["taskstore"]
    assert spec.renderer_provider is None


def test_configured_specs_dir_routes_new_lint_and_context(make_project):
    project = make_project()
    config_path = project.root / "mint.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "specsDir: .mint/specs",
            "specsDir: mint-specs",
        ),
        encoding="utf-8",
    )

    status, output = workflow.new_module("custom", root=project.root)

    assert status == 0, output
    assert "mint-specs/custom.mint.md" in output
    assert (project.root / "mint-specs" / "custom.mint.md").is_file()
    lint_status, lint_output = workflow.lint_module("custom", root=project.root)
    assert lint_status == 0, lint_output
    context = workflow.load_context("custom", project.root)
    assert context.spec.path == project.root / "mint-specs" / "custom.mint.md"


def test_new_module_can_scaffold_model_renderer_spec(make_project):
    project = make_project()
    status, output = workflow.new_module(
        "notes",
        renderer="model",
        model="mock-model",
        prompt_version="notes-v1",
        root=project.root,
    )

    assert status == 0, output
    assert "First live render/record: MINT_LIVE=1 mint live-smoke notes" in output
    text = project.spec_path("notes").read_text(encoding="utf-8")
    assert "rendererProvider: model" in text
    assert "rendererModel: mock-model" in text
    assert "rendererPromptVersion: notes-v1" in text
    spec = workflow.parse_spec_file(project.spec_path("notes"))
    assert spec.renderer_provider == "model"
    assert spec.renderer_model == "mock-model"
    assert spec.renderer_prompt_version == "notes-v1"


def test_new_module_can_scaffold_typescript_spec(make_project):
    project = make_project()
    status, output = workflow.new_module(
        "notes-ts",
        stack="typescript-lib",
        renderer="model",
        model="mock-model",
        prompt_version="notes-ts-v1",
        root=project.root,
    )

    assert status == 0, output
    text = project.spec_path("notes-ts").read_text(encoding="utf-8")
    assert "stack: typescript-lib" in text
    assert "tsc --noEmit" in text
    assert "Vitest" in text
    spec = workflow.parse_spec_file(project.spec_path("notes-ts"))
    assert spec.stack == "typescript-lib"


def test_new_module_can_scaffold_claude_cli_spec(make_project):
    project = make_project()
    status, output = workflow.new_module(
        "notes",
        renderer="claude-cli",
        model="sonnet",
        prompt_version="notes-v1",
        root=project.root,
    )

    assert status == 0, output
    assert "First live render/record: MINT_LIVE=1 mint live-smoke notes" in output
    text = project.spec_path("notes").read_text(encoding="utf-8")
    assert "rendererProvider: claude-cli" in text
    assert "rendererModel: sonnet" in text


def test_new_module_rejects_model_without_model_renderer(make_project):
    project = make_project()

    status, output = workflow.new_module("notes", model="mock-model", root=project.root)

    assert status == 1
    assert "--model and --prompt-version require a model renderer" in output


def test_new_module_requires_model_metadata_for_model_renderer(make_project):
    project = make_project()

    status, output = workflow.new_module("notes", renderer="model", root=project.root)

    assert status == 1
    assert "--renderer model requires both --model and --prompt-version" in output
    assert "MODEL_ID" in output


def test_new_module_rejects_placeholder_model_id(make_project):
    project = make_project()

    status, output = workflow.new_module(
        "notes",
        renderer="model",
        model="MODEL_ID",
        prompt_version="notes-v1",
        root=project.root,
    )

    assert status == 1
    assert "must be a real model id" in output


def test_live_smoke_cli_provider_does_not_require_anthropic_key(make_project, monkeypatch):
    project = make_project()
    workflow.new_module(
        "notes",
        renderer="claude-cli",
        model="sonnet",
        prompt_version="notes-v1",
        root=project.root,
    )
    monkeypatch.setenv("MINT_LIVE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MINT_CLAUDE_CLI_COMMAND", "definitely-not-a-mint-model-cli")

    status, output = workflow.live_smoke_module("notes", root=project.root)

    assert status == 1
    assert "ANTHROPIC_API_KEY" not in output
    assert "Claude CLI executable not found" in output


def test_next_guides_missing_spec_to_model_scaffold(make_project):
    project = make_project()

    status, output = workflow.next_module("notes", root=project.root)

    assert status == 0
    assert "State: no spec exists yet" in output
    assert "mint new notes --renderer model" in output


def test_next_project_guides_uninitialized_directory(tmp_path):
    status, output = workflow.next_module(root=tmp_path)

    assert status == 0
    assert "State: no Mint project found" in output
    assert "Next command: mint init --write" in output


def test_next_project_guides_empty_project(make_project):
    project = make_project()

    status, output = workflow.next_module(root=project.root)

    assert status == 0
    assert "State: project has no specs yet" in output
    assert "mint new MODULE --renderer model --model MODEL_ID" in output


def test_next_project_uses_single_seed_spec(tmp_path):
    status, output = workflow.init_project(write=True, root=tmp_path)
    assert status == 0, output

    status, output = workflow.next_module(root=tmp_path)

    assert status == 0
    assert "NEXT example" in output
    assert "Next command: mint render example" in output


def test_next_project_lists_multiple_specs(demo_project):
    status, output = workflow.next_module(root=demo_project.root)

    assert status == 0
    assert "State: project has 2 specs" in output
    assert "Next command: mint next <module>" in output
    assert "tasklist" in output and "taskstore" in output


def test_next_guides_local_spec_to_healthcheck_template_fix(make_project):
    project = make_project()
    workflow.new_module("notes", root=project.root)

    status, output = workflow.next_module("notes", root=project.root)

    assert status == 0
    assert "State: pre-render checks need attention" in output
    assert "Next command: mint healthcheck notes" in output
    assert "no deterministic template 'notes' exists" in output


def test_next_guides_model_spec_to_live_smoke_when_cassettes_missing(make_project):
    project = make_project()
    workflow.new_module(
        "notes",
        renderer="model",
        model="mock-model",
        prompt_version="notes-v1",
        root=project.root,
    )

    status, output = workflow.next_module("notes", root=project.root)

    assert status == 0
    assert "Next command: MINT_LIVE=1 mint live-smoke notes" in output
    assert "Replay cassettes missing for model renderer spec notes" in output


def test_next_guides_unrendered_valid_spec_to_render(demo_project):
    status, output = workflow.next_module("taskstore", root=demo_project.root)

    assert status == 0
    assert "State: ready to render" in output
    assert "Next command: mint render taskstore" in output


def test_next_guides_current_render_to_report(rendered_demo_project):
    status, output = workflow.next_module("taskstore", root=rendered_demo_project.root)

    assert status == 0
    assert "State: generated output is current" in output
    assert "Next command: mint report taskstore" in output


def test_healthcheck_fails_for_model_spec_without_replay_cassettes(make_project):
    project = make_project()
    workflow.new_module(
        "notes",
        renderer="model",
        model="mock-model",
        prompt_version="notes-v1",
        root=project.root,
    )

    status, output = workflow.healthcheck_module("notes", root=project.root)

    assert status == 1
    assert "Replay cassettes missing for model renderer spec notes" in output
    assert "MINT_LIVE=1 mint live-smoke notes" in output


def test_healthcheck_allows_missing_model_cassettes_in_live_mode(make_project, monkeypatch):
    project = make_project()
    workflow.new_module(
        "notes",
        renderer="model",
        model="mock-model",
        prompt_version="notes-v1",
        root=project.root,
    )
    monkeypatch.setenv("MINT_LIVE", "1")

    status, output = workflow.healthcheck_module("notes", root=project.root)

    assert status == 0, output
    assert "Replay cassettes missing" not in output


def test_doctor_fails_for_model_spec_without_replay_cassettes(make_project):
    project = make_project()
    workflow.new_module(
        "notes",
        renderer="model",
        model="mock-model",
        prompt_version="notes-v1",
        root=project.root,
    )

    status, output = workflow.doctor_project(root=project.root)

    assert status == 1
    assert "Replay cassettes missing for model renderer spec notes" in output


def test_doctor_guides_uninitialized_directory(tmp_path):
    status, output = workflow.doctor_project(root=tmp_path)

    assert status == 1
    assert "FAIL doctor" in output
    assert "Next command: mint init --write" in output
    assert "Then run: mint next" in output


def test_doctor_guides_initialized_project_without_specs(make_project):
    project = make_project()

    status, output = workflow.doctor_project(root=project.root)

    assert status == 1
    assert "No specs found under .mint/specs/" in output
    assert "mint new MODULE --renderer model --model MODEL_ID" in output
    assert "mint next MODULE" in output


def test_doctor_guides_missing_default_script(demo_project):
    (demo_project.root / "test_scripts" / "run_conformance_tests.sh").unlink()

    status, output = workflow.doctor_project(root=demo_project.root)

    assert status == 1
    assert "conformance script missing: test_scripts/run_conformance_tests.sh" in output
    assert "mint init --write" in output


def test_lint_flags_vague_acceptance(make_project):
    project = make_project()
    project.write_spec(
        "weak",
        """---
module: weak
description: weak spec
imports: []
requires: []
stack: python-lib
---

## definitions
- Thing: a thing.
## implementation
- Use Python 3.12.
## test
- pytest.
## functional
- id: FR1
  title: vague
  spec:
    - Do a useful thing.
  acceptance:
    - It works properly.
""",
    )

    status, output = workflow.lint_module("weak", root=project.root)

    assert status == 1
    assert "vague" in output
    assert "no testable assertion" in output


def test_lint_flags_relational_threshold_without_boundary_acceptance(make_project):
    project = make_project()
    project.write_spec(
        "bands",
        """---
module: bands
description: classify ratios
imports: []
requires: []
stack: python-lib
---

## definitions
- Band: a label for a ratio.
## implementation
- Provide ratio_band(ratio) in the bands package.
## test
- pytest.
## functional
- id: FR1
  title: classify near ratios
  spec:
    - ratio < 0.99 returns near.
  acceptance:
    - ratio_band(0.98) returns near.
""",
    )

    status, output = workflow.lint_module("bands", root=project.root)

    assert status == 1
    assert "relational threshold 0.99 needs an exact-boundary acceptance example" in output


def test_lint_accepts_exact_boundary_acceptance(make_project):
    project = make_project()
    project.write_spec(
        "bands",
        """---
module: bands
description: classify ratios
imports: []
requires: []
stack: python-lib
---

## definitions
- Band: a label for a ratio.
## implementation
- Provide ratio_band(ratio) in the bands package.
## test
- pytest.
## functional
- id: FR1
  title: classify near ratios
  spec:
    - ratio < 0.99 returns near.
  acceptance:
    - ratio_band(0.99) returns medium.
""",
    )

    status, output = workflow.lint_module("bands", root=project.root)

    assert status == 0, output


def test_lint_accepts_arrow_notation_as_testable_assertion(make_project):
    project = make_project()
    project.write_spec(
        "switches",
        """---
module: switches
description: switch predicates
imports: []
requires: []
stack: python-lib
---

## definitions
- Switch: boolean state.
## implementation
- Provide is_open(value) in the switches package.
## test
- pytest.
## functional
- id: FR1
  title: truthy values are open
  spec:
    - is_open(value) returns true for truthy inputs.
  acceptance:
    - is_open(3) → true.
""",
    )

    status, output = workflow.lint_module("switches", root=project.root)

    assert status == 0, output


def test_doctor_passes_for_demo_project(demo_project):
    status, output = workflow.doctor_project(root=demo_project.root)

    assert status == 0, output
    assert "PASS doctor" in output
    assert "pip:" in output
    assert "Spec: .mint/specs/taskstore.mint.md" in output


def test_doctor_fails_when_python_bin_has_no_pip(demo_project, tmp_path, monkeypatch):
    # The prepare script hard-requires `$PYTHON_BIN -m pip`; an interpreter without
    # pip (e.g. a bare uv venv) must fail doctor, not the first render.
    stub = tmp_path / "python-without-pip"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$2" = "pip" ]; then echo "No module named pip" >&2; exit 1; fi\n'
        'echo "pytest 9.0.0"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PYTHON_BIN", str(stub))

    status, output = workflow.doctor_project(root=demo_project.root)

    assert status == 1
    assert "pip is not runnable" in output
    assert "ensurepip" in output


def test_doctor_warns_for_local_spec_without_template(make_project):
    project = make_project()
    workflow.new_module("notes", root=project.root)

    status, output = workflow.doctor_project(root=project.root)

    assert status == 0, output
    assert "WARN" in output
    assert "no deterministic template 'notes' exists" in output
    assert "rendererProvider: model" in output


def test_healthcheck_fails_for_local_spec_without_template(make_project):
    project = make_project()
    workflow.new_module("notes", root=project.root)

    status, output = workflow.healthcheck_module("notes", root=project.root)

    assert status == 1
    assert "FAIL notes" in output
    assert "no deterministic template 'notes' exists" in output
    assert "MINT_LIVE=1 mint live-smoke notes" in output


def test_render_writes_run_report_and_report_command_reads_it(rendered_demo_project, monkeypatch):
    demo_project = rendered_demo_project
    monkeypatch.chdir(demo_project.root)

    report_path = demo_project.root / ".mint" / "generated" / "taskstore" / ".mintgen" / "reports" / "latest.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["module"] == "taskstore"
    assert report["totals"]["attempts"] >= 4
    assert report["units"][0]["wallClockSeconds"] is not None
    assert "tokens" in report["units"][0]
    assert report["units"][0]["testQuality"]["status"] == "passed"

    status, output = workflow.report_module("taskstore")
    assert status == 0
    assert "RUN REPORT taskstore" in output
    assert "Report JSON: .mint/generated/taskstore/.mintgen/reports/latest.json" in output


def test_generated_script_env_defaults_to_current_interpreter(make_project):
    project = make_project()
    project.write_spec("calc", CALC_SPEC)
    script = project.root / "test_scripts" / "print_python_bin.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$PYTHON_BIN\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    context = workflow.load_context("calc", project.root)

    adapter = context.stack_adapter
    result = stacks._run_project_script(
        context,
        "test_scripts/print_python_bin.sh",
        env=adapter.script_env(context, []),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.executable
    assert test_quality._script_env(context, [])["PYTHON_BIN"] == sys.executable


def test_mutation_probe_fails_when_baseline_conformance_script_fails(tmp_path):
    root = tmp_path
    generated = root / ".mint" / "generated" / "calc"
    conformance = root / "conformance" / "calc"
    (generated / "src" / "calc").mkdir(parents=True)
    conformance.mkdir(parents=True)
    (generated / "src" / "calc" / "__init__.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    scripts = root / "test_scripts"
    scripts.mkdir()
    unit = scripts / "unit.sh"
    unit.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    unit.chmod(0o755)
    conformance_script = scripts / "conformance.sh"
    conformance_script.write_text("#!/usr/bin/env bash\nexit 2\n", encoding="utf-8")
    conformance_script.chmod(0o755)
    context = SimpleNamespace(
        root=root,
        module="calc",
        generated_dir=generated,
        conformance_dir=conformance,
        config=SimpleNamespace(
            scripts=SimpleNamespace(
                unit="test_scripts/unit.sh",
                conformance="test_scripts/conformance.sh",
            ),
            test_quality=SimpleNamespace(
                mutation_probe=True,
                mutation_max_candidates=3,
            ),
        ),
    )

    verdict = test_quality._run_mutation_probe(context, required_src=[])

    assert verdict["status"] == "failed"
    assert "mutation baseline test run failed" in verdict["reason"]
    assert verdict["baseline"]["conformance"]["exitCode"] == 2


# --------------------------------------------------------------------------- #
# model renderer path (offline, scripted) — retry & no-tests gate
# --------------------------------------------------------------------------- #

CALC_SPEC = """---
module: calc
description: tiny adder
imports: []
requires: []
stack: python-lib
---

## definitions
- Add: adds two integers.
## implementation
- Provide add(a, b) in the calc package.
## test
- pytest unit and conformance tests.
## functional
- id: FR1
  title: add returns the sum
  spec:
    - add(a, b) returns a + b.
  acceptance:
    - add(2, 3) == 5 and add(10, 5) == 15.
"""

CALC_TWO_UNIT_SPEC = """---
module: calc
description: tiny arithmetic
imports: []
requires: []
stack: python-lib
---

## definitions
- Add: adds two integers.
- Subtract: subtracts one integer from another.
## implementation
- Provide add(a, b) and sub(a, b) in the calc package.
## test
- pytest unit and conformance tests.
## functional
- id: FR1
  title: add returns the sum
  spec:
    - add(a, b) returns a + b.
  acceptance:
    - add(2, 3) == 5 and add(10, 5) == 15.
- id: FR2
  title: sub returns the difference
  spec:
    - sub(a, b) returns a - b.
  acceptance:
    - sub(10, 3) == 7 and sub(5, 8) == -3.
"""

_CONFTEST = (
    "import sys\nfrom pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
)


def calc_patch(add_body: str, *, with_tests: bool = True, weak_tests: bool = False) -> str:
    unit_test = (
        "def test_placeholder():\n    assert True\n"
        if weak_tests
        else "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    conformance_test = (
        "def test_placeholder_conf():\n    assert True\n"
        if weak_tests
        else "from calc import add\n\n\ndef test_add_conf():\n    assert add(10, 5) == 15\n"
    )
    files = [
        {
            "path": "src/calc/__init__.py",
            "action": "write",
            "contents": f"def add(a, b):\n    return {add_body}\n",
        },
        {"path": "tests/conftest.py", "action": "write", "contents": _CONFTEST},
        {
            "path": "FR1/test_fr1.py",
            "action": "write",
            "contents": conformance_test,
            "root": "conformance",
        },
    ]
    if with_tests:
        files.append(
            {
                "path": "tests/test_fr1.py",
                "action": "write",
                "contents": unit_test,
            }
        )
    return json.dumps({"summary": "calc render", "files": files})


def calc_two_unit_patch(unit: str) -> str:
    if unit == "FR1":
        source = "def add(a, b):\n    return a + b\n"
        files = [
            {
                "path": "src/calc/__init__.py",
                "action": "write",
                "contents": source,
            },
            {"path": "tests/conftest.py", "action": "write", "contents": _CONFTEST},
            {
                "path": "tests/test_fr1.py",
                "action": "write",
                "contents": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            },
            {
                "path": "FR1/test_fr1.py",
                "action": "write",
                "contents": "from calc import add\n\n\ndef test_add_conf():\n    assert add(10, 5) == 15\n",
                "root": "conformance",
            },
        ]
    else:
        source = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
        files = [
            {
                "path": "src/calc/__init__.py",
                "action": "write",
                "contents": source,
            },
            {
                "path": "tests/test_fr2.py",
                "action": "write",
                "contents": "from calc import sub\n\n\ndef test_sub():\n    assert sub(10, 3) == 7\n",
            },
            {
                "path": "FR2/test_fr2.py",
                "action": "write",
                "contents": "from calc import sub\n\n\ndef test_sub_conf():\n    assert sub(5, 8) == -3\n",
                "root": "conformance",
            },
        ]
    return json.dumps({"summary": f"calc {unit}", "files": files})


def make_calc_project(make_project, provider="model"):
    project = make_project(provider=provider, model="mock-model")
    project.write_spec("calc", CALC_SPEC)
    return project


def make_calc_two_unit_project(make_project):
    project = make_project(provider="model", model="mock-model")
    project.write_spec("calc", CALC_TWO_UNIT_SPEC)
    return project


def set_limit(project, key: str, value: int) -> None:
    path = project.root / "mint.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(f"{key}: 0", f"{key}: {value}")
    path.write_text(text, encoding="utf-8")


def test_model_renderer_unit_retry_with_feedback(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    # Attempt 1: wrong impl (a - b) -> unit test fails. Attempt 2: correct.
    client = ScriptedModelClient(
        {
            "FR1:unit:1": calc_patch("a - b"),
            "FR1:unit:2": calc_patch("a + b"),
        }
    )
    status, output = workflow.render_module("calc", model_client=client)
    assert status == 0, output
    assert ("FR1", "unit", 1) in client.calls and ("FR1", "unit", 2) in client.calls
    meta = project.metadata("calc")
    assert meta["functionalUnits"][0]["attempts"]["unit"] == 2
    assert meta["model"] == "mock-model"
    # Both attempts left an audit trail.
    attempts = project.root / ".mint" / "generated" / "calc" / ".mintgen" / "attempts" / "FR1"
    assert (attempts / "unit-1.prompt.txt").exists()
    assert (attempts / "unit-1.response.txt").exists()
    assert (attempts / "unit-2.patch.json").exists()
    attempt_data = json.loads((attempts / "unit-1.json").read_text())
    assert attempt_data["cassetteId"]
    assert meta["functionalUnits"][0]["testQuality"]["status"] == "passed"


def test_model_renderer_patch_validation_retry_with_feedback(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    client = ScriptedModelClient(
        {
            "FR1:unit:1": json.dumps({"summary": "bad", "files": []}),
            "FR1:unit:2": calc_patch("a + b"),
        }
    )

    status, output = workflow.render_module("calc", model_client=client)

    assert status == 0, output
    assert ("FR1", "unit", 1) in client.calls
    assert ("FR1", "unit", 2) in client.calls
    attempts = project.root / ".mint" / "generated" / "calc" / ".mintgen" / "attempts" / "FR1"
    first = json.loads((attempts / "unit-1.json").read_text())
    assert first["classification"] == "patch_invalid"
    assert "non-empty 'files'" in first["patchValidationError"]
    retry_prompt = (attempts / "unit-2.prompt.txt").read_text()
    assert "previous response did not satisfy the renderer patch contract" in retry_prompt


def test_attempt_budget_aborts_with_report(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    set_limit(project, "maxRenderAttempts", 1)
    monkeypatch.chdir(project.root)
    # Only real renders consume the attempt budget. Attempt 1 fails the unit test,
    # forcing a second render; that second render trips the (2/1) attempt budget
    # before any non-render step is reached.
    client = ScriptedModelClient(
        {
            "FR1:unit:1": calc_patch("a - b"),
            "FR1:unit:2": calc_patch("a + b"),
        }
    )

    status, output = workflow.render_module("calc", model_client=client)

    assert status == 1
    assert "Render budget exceeded" in output
    assert "attempt budget exceeded" in output
    report = json.loads(
        (
            project.root
            / ".mint" / "generated"
            / "calc"
            / ".mintgen"
            / "reports"
            / "budget-abort.json"
        ).read_text()
    )
    assert report["reason"] == "attempt budget exceeded (2/1)"
    assert report["attempts"] == 2


def test_token_budget_aborts_with_report(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    set_limit(project, "maxRenderTokensEstimate", 1)
    monkeypatch.chdir(project.root)
    client = ScriptedModelClient({"default": calc_patch("a + b")})

    status, output = workflow.render_module("calc", model_client=client)

    assert status == 1
    assert "Render budget exceeded" in output
    assert "token budget exceeded" in output
    report = json.loads(
        (
            project.root
            / ".mint" / "generated"
            / "calc"
            / ".mintgen"
            / "reports"
            / "budget-abort.json"
        ).read_text()
    )
    assert report["tokensEstimate"] > 1
    assert report["maxTokensEstimate"] == 1


def test_model_renderer_conformance_retry(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    # Unit passes on a constant impl (2+3 "==5" by luck) but conformance (10+5)
    # fails, forcing a conformance-phase re-render that fixes it.
    client = ScriptedModelClient(
        {
            "FR1:unit:1": calc_patch("5"),
            "FR1:conformance:2": calc_patch("a + b"),
        }
    )
    status, output = workflow.render_module("calc", model_client=client)
    assert status == 0, output
    assert ("FR1", "conformance", 2) in client.calls
    meta = project.metadata("calc")
    assert meta["functionalUnits"][0]["attempts"]["conformance"] == 2


def test_rendered_prompts_never_embed_project_paths(make_calc_project_factory, monkeypatch):
    # Regression for GH issue #1: prompts (and therefore cassette ids/contents)
    # must be a pure function of the spec, not the checkout location, or replay
    # breaks on any other machine. Retry feedback embeds raw pytest output, which
    # is where absolute tmp paths leaked in.
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    client = ScriptedModelClient(
        {
            "FR1:unit:1": calc_patch("a - b"),  # unit test fails -> feedback prompt
            "FR1:unit:2": calc_patch("5"),  # unit passes by luck, conformance fails
            "FR1:conformance:2": calc_patch("a + b"),
        }
    )

    status, output = workflow.render_module("calc", model_client=client)

    assert status == 0, output
    attempts_dir = project.root / ".mint" / "generated" / "calc" / ".mintgen" / "attempts"
    prompts = sorted(attempts_dir.rglob("*.prompt.txt"))
    assert len(prompts) >= 3
    for prompt_path in prompts:
        text = prompt_path.read_text(encoding="utf-8")
        assert str(project.root) not in text, (
            f"absolute project path leaked into {prompt_path.name}"
        )


def test_aborted_render_resumes_from_last_good_checkpoint(make_project, monkeypatch):
    project = make_calc_two_unit_project(make_project)
    monkeypatch.chdir(project.root)
    first_client = ScriptedModelClient({"FR1:unit:1": calc_two_unit_patch("FR1")})

    status, output = workflow.render_module("calc", model_client=first_client)

    assert status == 1
    assert "Completed FR1" in output
    assert "FAILED FR2" in output
    meta = project.metadata("calc")
    assert meta["lastSuccessfulUnitId"] == "FR1"
    fr1_commit = meta["functionalUnits"][0]["finishedCommit"]

    second_client = ScriptedModelClient({"FR2:unit:1": calc_two_unit_patch("FR2")})
    status, output = workflow.render_module("calc", model_client=second_client)

    assert status == 0, output
    assert "Range: FR2:FR2" in output
    assert "new functional unit FR2" in output
    assert second_client.calls == [("FR2", "unit", 1)]
    after = project.metadata("calc")
    assert after["functionalUnits"][0]["finishedCommit"] == fr1_commit
    assert after["functionalUnits"][1]["id"] == "FR2"


def test_no_tests_discovered_fails(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    # Never ship a unit test -> the unit gate must fail, even though pytest exits 5.
    client = ScriptedModelClient({"default": calc_patch("a + b", with_tests=False)})
    status, output = workflow.render_module("calc", model_client=client)
    assert status == 1
    assert "No tests were discovered" in output


def test_test_quality_gate_fails_shallow_tests(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    client = ScriptedModelClient({"default": calc_patch("a + b", weak_tests=True)})

    status, output = workflow.render_module("calc", model_client=client)

    assert status == 1
    assert "test-quality gate failed" in output
    assert "acceptance criteria missing test references" in output
    meta = project.metadata("calc")
    record = meta["functionalUnits"][0]
    assert record["status"] == "test_quality_failed"
    assert record["testQuality"]["status"] == "failed"
    attempt = (
        project.root
        / ".mint" / "generated"
        / "calc"
        / ".mintgen"
        / "attempts"
        / "FR1"
        / "test-quality-1.json"
    )
    attempt_data = json.loads(attempt.read_text())
    assert attempt_data["classification"] == "test_quality_failed"
    assert attempt_data["testQuality"]["mutation"]["status"] == "failed"


def test_unit_failure_after_retries_reports(make_calc_project_factory, monkeypatch):
    project = make_calc_project_factory()
    monkeypatch.chdir(project.root)
    client = ScriptedModelClient({"default": calc_patch("a - b")})  # always wrong
    status, output = workflow.render_module("calc", model_client=client)
    assert status == 1
    assert "FAILED FR1" in output
    assert "unit tests failed" in output.lower()


@pytest.fixture
def make_calc_project_factory(make_project):
    def _factory():
        return make_calc_project(make_project)

    return _factory


# --------------------------------------------------------------------------- #
# render-plan guards: dead-knob enforcement, no-checkpoint, explicit-range drift
# --------------------------------------------------------------------------- #


def test_max_functional_units_per_render_is_enforced(make_project):
    project = make_project()
    project.write_spec("calc", CALC_TWO_UNIT_SPEC)
    mint_yaml = project.root / "mint.yaml"
    mint_yaml.write_text(
        mint_yaml.read_text(encoding="utf-8").replace(
            "maxFunctionalUnitsPerRender: 20", "maxFunctionalUnitsPerRender: 1"
        ),
        encoding="utf-8",
    )
    context = workflow.load_context("calc", project.root)
    plan = workflow.RenderPlan(0, 1, "test")  # 2 units, cap is 1

    with pytest.raises(MintError, match="maxFunctionalUnitsPerRender"):
        workflow._enforce_max_units_per_render(context, plan)


def test_explicit_from_without_metadata_refuses(make_project):
    project = make_project()
    project.write_spec("calc", CALC_TWO_UNIT_SPEC)
    context = workflow.load_context("calc", project.root)
    hashes = workflow.compute_context_hashes(context)

    with pytest.raises(MintError, match="no checkpoint recorded"):
        workflow.determine_render_plan(context, None, "FR2", None, False, hashes)


def test_explicit_range_refuses_on_context_drift(make_project):
    project = make_project()
    project.write_spec("calc", CALC_TWO_UNIT_SPEC)
    context = workflow.load_context("calc", project.root)
    hashes = workflow.compute_context_hashes(context)
    metadata = {
        "nonFunctionalSpecHash": "stale-does-not-match",
        "importedContextHash": hashes.imported_context_hash,
        "requiredModuleCodeHash": hashes.required_module_code_hash,
        "functionalUnits": [],
    }

    with pytest.raises(MintError, match="context inputs changed"):
        workflow.determine_render_plan(context, metadata, None, "FR2:FR2", False, hashes)


def test_module_render_lock_is_exclusive(tmp_path):
    from mint_cli.state import module_render_lock

    module_dir = tmp_path / "generated" / "calc"
    module_dir.mkdir(parents=True)

    with module_render_lock(module_dir):
        # A second concurrent render of the same module must fail loudly, not
        # silently interleave checkpoint reset / cleanup / commit and corrupt state.
        with pytest.raises(MintError, match="already in progress"):
            with module_render_lock(module_dir):
                pass

    # Once released, the lock is re-acquirable — no stale lock left behind.
    with module_render_lock(module_dir):
        pass


def test_module_render_lock_survives_workspace_wipe(tmp_path):
    # The lock lives outside module_dir, so wiping the workspace mid-hold (as a
    # full render does) must not release it or break exclusion.
    import shutil

    from mint_cli.state import module_render_lock

    module_dir = tmp_path / "generated" / "calc"
    module_dir.mkdir(parents=True)

    with module_render_lock(module_dir):
        shutil.rmtree(module_dir)
        module_dir.mkdir(parents=True)
        with pytest.raises(MintError, match="already in progress"):
            with module_render_lock(module_dir):
                pass
