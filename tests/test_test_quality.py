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
