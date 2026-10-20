"""`mint env` / `mint run` / `mint exec` / `mint render --all`: composing a rendered
module graph into something you can run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mint_cli import cli, workflow
from mint_cli.errors import MintError
from mint_cli.specs import parse_spec_file


def _add_frontmatter_key(spec_path: Path, line: str) -> None:
    text = spec_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    spec_path.write_text(text.replace("---\n", f"---\n{line}\n", 1), encoding="utf-8")


def test_entrypoint_key_is_parsed_but_never_hashed(demo_project) -> None:
    path = demo_project.spec_path("tasklist")
    before = parse_spec_file(path)
    _add_frontmatter_key(path, "entrypoint: tasklist.cli")
    after = parse_spec_file(path)

    assert before.entrypoint is None
    assert after.entrypoint == "tasklist.cli"
    assert after.spec_hash == before.spec_hash
    assert after.non_functional_hash == before.non_functional_hash
    assert "entrypoint" not in json.dumps(after.to_ir())


@pytest.mark.parametrize("bad", ["tasklist/cli", "1abc", "a..b", "a.b-c"])
def test_entrypoint_key_rejects_non_dotted_names(demo_project, bad: str) -> None:
    path = demo_project.spec_path("tasklist")
    _add_frontmatter_key(path, f"entrypoint: {bad}")
    with pytest.raises(MintError, match="Invalid entrypoint"):
        parse_spec_file(path)


def test_env_requires_a_rendered_graph(demo_project) -> None:
    with pytest.raises(MintError, match=r"Not rendered: tasklist, taskstore.*mint render tasklist"):
        workflow.resolve_app_environment("tasklist", demo_project.root)


def test_env_composes_module_then_requires_in_dependency_order(rendered_demo_project) -> None:
    root = rendered_demo_project.root
    app = workflow.resolve_app_environment("tasklist", root)

    generated = root / ".mint" / "generated"
    assert app.modules == ("tasklist", "taskstore")
    assert app.src_paths == (generated / "tasklist" / "src", generated / "taskstore" / "src")
    assert app.deps_paths == ()
    assert app.stale == ()
    assert app.entrypoint is None

    env = workflow.environment_variables(app, base={"PYTHONPATH": "/elsewhere", "HOME": "/h"})
    assert env["PYTHONPATH"] == os.pathsep.join([str(app.src_paths[0]), str(app.src_paths[1]), "/elsewhere"])
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["MINT_APP_MODULE"] == "tasklist"
    assert env["MINT_APP_ROOT"] == str(root)
    assert env["HOME"] == "/h"


def test_env_includes_module_local_deps_dirs(rendered_demo_project) -> None:
    root = rendered_demo_project.root
    deps = root / ".mint" / "generated" / "taskstore" / ".mint-deps"
    deps.mkdir()
    app = workflow.resolve_app_environment("tasklist", root)
    assert app.deps_paths == (deps,)
    assert app.pythonpath.endswith(os.pathsep + str(deps))


def test_env_formats(rendered_demo_project) -> None:
    app = workflow.resolve_app_environment("tasklist", rendered_demo_project.root)

    shell = workflow.format_environment(app, "shell")
    assert shell.startswith("# mint env tasklist: tasklist, taskstore\n")
    assert "export PYTHONPATH=" in shell and "export MINT_APP_MODULE=tasklist\n" in shell
    assert "stale" not in shell

    assert workflow.format_environment(app, "path") == app.pythonpath + "\n"

    payload = json.loads(workflow.format_environment(app, "json"))
    assert payload["modules"] == ["tasklist", "taskstore"]
    assert payload["pythonpath"] == [str(p) for p in app.src_paths]
    assert payload["env"]["PYTHONPATH"] == app.pythonpath
    assert payload["stale"] == [] and payload["entrypoint"] is None

    with pytest.raises(MintError, match="Unknown env format"):
        workflow.format_environment(app, "yaml")


def test_env_reports_stale_modules_after_a_spec_edit(rendered_demo_project, capsys) -> None:
    root = rendered_demo_project.root
    spec = rendered_demo_project.spec_path("taskstore")
    spec.write_text(spec.read_text(encoding="utf-8") + "\n- Extra bullet moves the hash.\n", encoding="utf-8")

    app = workflow.resolve_app_environment("tasklist", root)
    assert app.stale == ("taskstore",)
    assert "# stale (re-render with `mint render tasklist`): taskstore" in workflow.format_environment(app)

    workflow.warn_if_stale(app)
    assert "running stale generated code for taskstore" in capsys.readouterr().err


def test_env_refuses_non_python_stacks(rendered_demo_project) -> None:
    spec = rendered_demo_project.spec_path("taskstore")
    spec.write_text(spec.read_text(encoding="utf-8").replace("stack: python-lib", "stack: typescript-lib"), encoding="utf-8")
    with pytest.raises(MintError, match=r"Python stacks only; not Python: taskstore \(typescript-lib\)"):
        workflow.resolve_app_environment("tasklist", rendered_demo_project.root)


def test_entrypoint_resolution_order_and_errors(rendered_demo_project) -> None:
    root = rendered_demo_project.root
    app = workflow.resolve_app_environment("tasklist", root)

    # The template ships tasklist/cli.py but no tasklist/__main__.py.
    with pytest.raises(MintError, match=r"declares no entrypoint.*Runnable candidates: tasklist\.cli"):
        workflow.resolve_entrypoint(app)
    assert workflow.resolve_entrypoint(app, "tasklist.cli") == "tasklist.cli"
    with pytest.raises(MintError, match=r"Entrypoint 'nope\.cli' is not runnable"):
        workflow.resolve_entrypoint(app, "nope.cli")

    _add_frontmatter_key(rendered_demo_project.spec_path("tasklist"), "entrypoint: tasklist.cli")
    app = workflow.resolve_app_environment("tasklist", root)
    assert app.stale == (), "entrypoint is runtime wiring: adding it must not make the module stale"
    assert workflow.resolve_entrypoint(app) == "tasklist.cli"
    assert workflow.resolve_entrypoint(app, "taskstore.store") == "taskstore.store"

    (root / ".mint" / "generated" / "tasklist" / "src" / "tasklist" / "__main__.py").write_text(
        "from .cli import main\nraise SystemExit(main())\n", encoding="utf-8"
    )
    app = workflow.resolve_app_environment("tasklist", root)
    assert workflow.resolve_entrypoint(app, "tasklist") == "tasklist"


def test_run_app_executes_the_entrypoint_with_the_composed_path(rendered_demo_project, tmp_path, monkeypatch) -> None:
    root = rendered_demo_project.root
    monkeypatch.setenv("TASKLIST_STORE", str(tmp_path / "store.json"))
    monkeypatch.setenv("PYTHON_BIN", sys.executable)
    assert workflow.run_app("tasklist", ["add", "write docs"], root=root, entrypoint="tasklist.cli") == 0
    assert json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))

    # The child's exit code is the command's exit code.
    assert workflow.run_app("tasklist", ["--definitely-not-a-command"], root=root, entrypoint="tasklist.cli") == 2


def test_exec_in_app_runs_any_command_in_the_environment(rendered_demo_project, tmp_path) -> None:
    root = rendered_demo_project.root
    out = tmp_path / "out.txt"
    script = (
        "import os, pathlib, tasklist, taskstore;"
        f"pathlib.Path({str(out)!r}).write_text(os.environ['MINT_APP_MODULE'] + ' ' + os.environ['PYTHONDONTWRITEBYTECODE'])"
    )
    assert workflow.exec_in_app("tasklist", [sys.executable, "-c", script], root=root) == 0
    assert out.read_text() == "tasklist 1"

    with pytest.raises(MintError, match="mint exec needs a command"):
        workflow.exec_in_app("tasklist", [], root=root)
    with pytest.raises(MintError, match="Command not found: definitely-not-a-binary"):
        workflow.exec_in_app("tasklist", ["definitely-not-a-binary"], root=root)


def test_render_all_renders_every_spec_in_dependency_order(demo_project) -> None:
    status, output = workflow.render_all(root=demo_project.root)
    assert status == 0, output
    assert output.index("RENDER taskstore") < output.index("RENDER tasklist")

    status, output = workflow.render_all(root=demo_project.root)
    assert status == 0
    assert "NOOP taskstore" in output and "NOOP tasklist" in output


def test_render_all_without_specs_explains(make_project) -> None:
    with pytest.raises(MintError, match=r"No specs found under \.mint/specs/"):
        workflow.render_all(root=make_project().root)


# --- CLI surface ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "own", "passthrough"),
    [
        (["run", "m", "--", "--help"], ["run", "m"], ["--help"]),
        (["run", "m", "--entry", "x.y", "add", "a", "--", "--flag"], ["run", "m", "--entry", "x.y", "add", "a"], ["--flag"]),
        (["exec", "m", "--", "python", "-c", "1", "--", "x"], ["exec", "m"], ["python", "-c", "1", "--", "x"]),
        (["render", "m", "--", "x"], ["render", "m", "--", "x"], []),
        (["run", "m"], ["run", "m"], []),
    ],
)
def test_split_passthrough(argv, own, passthrough) -> None:
    assert cli.split_passthrough(argv) == (own, passthrough)


def test_cli_env_run_exec_and_render_all(rendered_demo_project, monkeypatch, capsys, tmp_path) -> None:
    root = rendered_demo_project.root
    monkeypatch.chdir(root)
    monkeypatch.setenv("TASKLIST_STORE", str(tmp_path / "store.json"))
    monkeypatch.setenv("PYTHON_BIN", sys.executable)

    assert cli.main(["env", "tasklist", "--format", "path"]) == 0
    assert capsys.readouterr().out.strip().startswith(str(root / ".mint" / "generated" / "tasklist" / "src"))

    assert cli.main(["run", "tasklist", "--entry", "tasklist.cli", "add", "ship it"]) == 0
    assert cli.main(["exec", "tasklist", "--", sys.executable, "-c", "import tasklist"]) == 0

    assert cli.main(["run", "tasklist"]) == 1
    assert "declares no entrypoint" in capsys.readouterr().err

    assert cli.main(["render", "--all"]) == 0
    assert "NOOP tasklist" in capsys.readouterr().out
    assert cli.main(["render", "--all", "--from", "FR1"]) == 2
    assert "--from/--range need a single module" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli.main(["render"])
    assert exc.value.code == 2


def test_cli_run_passes_flags_after_separator_to_the_app(rendered_demo_project, tmp_path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["TASKLIST_STORE"] = str(tmp_path / "store.json")
    env["MINT_PROGRESS"] = "0"
    result = subprocess.run(
        [sys.executable, "-m", "mint_cli", "run", "tasklist", "--entry", "tasklist.cli", "--", "--help"],
        cwd=rendered_demo_project.root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: tasklist"), result.stdout
