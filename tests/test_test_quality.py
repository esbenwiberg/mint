from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mint_cli import test_quality
from mint_cli.stacks import PythonStackAdapter


def _mutation_context(tmp_path: Path) -> SimpleNamespace:
    generated = tmp_path / ".mint" / "generated" / "calc"
    conformance = tmp_path / "conformance" / "calc"
    (generated / "src" / "calc").mkdir(parents=True)
    conformance.mkdir(parents=True)
    (generated / "src" / "calc" / "__init__.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        root=tmp_path,
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
                mutation_max_candidates=1,
            ),
        ),
    )


def test_mutation_probe_short_circuits_conformance_after_unit_kills_mutation(
    tmp_path, monkeypatch
):
    context = _mutation_context(tmp_path)
    calls = {"unit": 0, "conformance": 0}

    def fake_run_project_script(context, script, required_src):
        mutated = "mint mutation probe" in (
            context.generated_dir / "src" / "calc" / "__init__.py"
        ).read_text(encoding="utf-8")
        if script == context.config.scripts.unit:
            calls["unit"] += 1
            return SimpleNamespace(returncode=1 if mutated else 0, stdout="", stderr="")
        calls["conformance"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(test_quality, "_run_project_script", fake_run_project_script)

    verdict = test_quality._run_mutation_probe(context, required_src=[])

    assert verdict["status"] == "passed"
    assert calls == {"unit": 2, "conformance": 1}


def test_mutation_probe_can_reuse_recent_unit_conformance_pass(tmp_path, monkeypatch):
    context = _mutation_context(tmp_path)
    calls = {"unit": 0, "conformance": 0}

    def fake_run_project_script(context, script, required_src):
        mutated = "mint mutation probe" in (
            context.generated_dir / "src" / "calc" / "__init__.py"
        ).read_text(encoding="utf-8")
        if script == context.config.scripts.unit:
            calls["unit"] += 1
            return SimpleNamespace(returncode=1 if mutated else 0, stdout="", stderr="")
        calls["conformance"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(test_quality, "_run_project_script", fake_run_project_script)

    verdict = test_quality._run_mutation_probe(
        context,
        required_src=[],
        baseline_already_passed=True,
    )

    assert verdict["status"] == "passed"
    assert calls == {"unit": 1, "conformance": 0}


def test_internal_python_script_env_skips_redundant_pytest_probe(tmp_path):
    context = _mutation_context(tmp_path)
    direct_env = test_quality._script_env(context, [])
    adapter_env = PythonStackAdapter().script_env(context, [])

    assert direct_env["MINT_SKIP_PYTEST_VERSION_CHECK"] == "1"
    assert adapter_env["MINT_SKIP_PYTEST_VERSION_CHECK"] == "1"


def test_coverage_trace_counts_lines_run_in_worker_threads(tmp_path):
    # In-process HTTP test clients (fastapi/starlette TestClient) execute route
    # handlers on anyio portal threads. sys.settrace alone never sees those
    # frames, so generated code exercised only through such a client measured as
    # uncovered; the trace runner must install threading.settrace as well.
    generated = tmp_path / ".mint" / "generated" / "threaded-mod"
    conformance = tmp_path / "conformance" / "threaded-mod"
    src = generated / "src" / "threaded_mod"
    src.mkdir(parents=True)
    (generated / "tests").mkdir()
    conformance.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "import threading\n"
        "\n"
        "\n"
        "def compute():\n"
        "    value = 2 + 3\n"
        "    return value * 2\n"
        "\n"
        "\n"
        "def run_in_thread():\n"
        "    results = []\n"
        "\n"
        "    def worker():\n"
        "        results.append(compute())\n"
        "\n"
        "    thread = threading.Thread(target=worker)\n"
        "    thread.start()\n"
        "    thread.join()\n"
        "    return results[0]\n",
        encoding="utf-8",
    )
    (generated / "tests" / "test_threaded.py").write_text(
        "from threaded_mod import run_in_thread\n"
        "\n"
        "\n"
        "def test_run_in_thread():\n"
        "    assert run_in_thread() == 10\n",
        encoding="utf-8",
    )
    (conformance / "test_conformance.py").write_text(
        "from threaded_mod import run_in_thread\n"
        "\n"
        "\n"
        "def test_conformance():\n"
        "    assert run_in_thread() == 10\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        root=tmp_path,
        module="threaded-mod",
        generated_dir=generated,
        conformance_dir=conformance,
        config=SimpleNamespace(
            test_quality=SimpleNamespace(min_coverage_percent=60),
        ),
    )

    verdict = test_quality._measure_coverage(context, required_src=[])

    assert verdict["status"] == "passed"
    # compute()'s body runs only on the worker thread; if the trace misses
    # threads, these lines drop out and coverage collapses to import-time lines.
    assert verdict["percent"] == 100.0


def _coverage_fixture(context, unit_lines, conformance_lines):
    return {
        "unit": {"calc/__init__.py": set(unit_lines)},
        "conformance": {"calc/__init__.py": set(conformance_lines)},
    }


def test_mutation_probe_skips_every_launch_when_no_test_executes_the_body(tmp_path, monkeypatch):
    context = _mutation_context(tmp_path)
    calls = {"unit": 0, "conformance": 0}

    def fake_run_project_script(context, script, required_src):
        calls["unit" if script == context.config.scripts.unit else "conformance"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(test_quality, "_run_project_script", fake_run_project_script)

    # add() lives on lines 1-2; only the def line (1) was ever executed.
    verdict = test_quality._run_mutation_probe(
        context,
        required_src=[],
        baseline_already_passed=True,
        coverage_by_target=_coverage_fixture(context, [1], []),
    )

    assert verdict["status"] == "failed"
    assert verdict["testLaunches"] == 0
    assert verdict["survivors"][0]["reason"] == "no test executes this function body"
    assert "never executed" in verdict["reason"]
    assert calls == {"unit": 0, "conformance": 0}
    # The mutation must have been rolled back even though nothing ran.
    source = (context.generated_dir / "src" / "calc" / "__init__.py").read_text(encoding="utf-8")
    assert "mint mutation probe" not in source


def test_mutation_probe_runs_only_the_suite_that_executes_the_body(tmp_path, monkeypatch):
    context = _mutation_context(tmp_path)
    calls = {"unit": 0, "conformance": 0}

    def fake_run_project_script(context, script, required_src):
        mutated = "mint mutation probe" in (
            context.generated_dir / "src" / "calc" / "__init__.py"
        ).read_text(encoding="utf-8")
        if script == context.config.scripts.unit:
            calls["unit"] += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        calls["conformance"] += 1
        return SimpleNamespace(returncode=1 if mutated else 0, stdout="", stderr="")

    monkeypatch.setattr(test_quality, "_run_project_script", fake_run_project_script)

    # Only the conformance suite reaches add()'s body (line 2).
    verdict = test_quality._run_mutation_probe(
        context,
        required_src=[],
        baseline_already_passed=True,
        coverage_by_target=_coverage_fixture(context, [], [1, 2]),
    )

    assert verdict["status"] == "passed"
    assert verdict["testLaunches"] == 1
    assert calls == {"unit": 0, "conformance": 1}


def test_mutation_probe_without_coverage_data_runs_both_suites(tmp_path, monkeypatch):
    context = _mutation_context(tmp_path)
    calls = {"unit": 0, "conformance": 0}

    def fake_run_project_script(context, script, required_src):
        calls["unit" if script == context.config.scripts.unit else "conformance"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(test_quality, "_run_project_script", fake_run_project_script)

    verdict = test_quality._run_mutation_probe(
        context, required_src=[], baseline_already_passed=True, coverage_by_target=None
    )

    assert verdict["status"] == "failed"
    assert verdict["testLaunches"] == 2
    assert calls == {"unit": 1, "conformance": 1}


def test_coverage_verdict_carries_private_per_target_lines(tmp_path):
    generated = tmp_path / ".mint" / "generated" / "mod"
    conformance = tmp_path / "conformance" / "mod"
    src = generated / "src" / "mod"
    src.mkdir(parents=True)
    (generated / "tests").mkdir()
    conformance.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "def unit_only():\n    return 1\n\n\ndef conf_only():\n    return 2\n", encoding="utf-8"
    )
    (generated / "tests" / "test_u.py").write_text(
        "from mod import unit_only\n\n\ndef test_u():\n    assert unit_only() == 1\n",
        encoding="utf-8",
    )
    (conformance / "test_c.py").write_text(
        "from mod import conf_only\n\n\ndef test_c():\n    assert conf_only() == 2\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        root=tmp_path,
        module="mod",
        generated_dir=generated,
        conformance_dir=conformance,
        config=SimpleNamespace(test_quality=SimpleNamespace(min_coverage_percent=60)),
    )

    verdict = test_quality._measure_coverage(context, required_src=[])

    by_target = verdict["_byTarget"]
    assert 2 in by_target["unit"]["mod/__init__.py"]
    assert 6 not in by_target["unit"]["mod/__init__.py"]
    assert 6 in by_target["conformance"]["mod/__init__.py"]
    assert 2 not in by_target["conformance"]["mod/__init__.py"]
