from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def run_mint(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONPYCACHEPREFIX"] = str(root / ".pytest_cache" / "pycache")
    return subprocess.run(
        [sys.executable, "-m", "mint_cli", *args],
        cwd=root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )


@pytest.mark.e2e
def test_cli_e2e_init_write_seeds_offline_example(tmp_path) -> None:
    initialized = run_mint(tmp_path, "init", "--write")
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    assert "Wrote .mint/specs/example.mint.md" in initialized.stdout
    assert "First smoke test: mint render example" in initialized.stdout
    assert "Guided next step: mint next" in initialized.stdout

    doctor = run_mint(tmp_path, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "PASS doctor" in doctor.stdout

    linted = run_mint(tmp_path, "lint", "example")
    assert linted.returncode == 0, linted.stdout + linted.stderr
    assert "PASS lint example" in linted.stdout

    rendered = run_mint(tmp_path, "render", "example")
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    assert "Completed FR2" in rendered.stdout

    next_step = run_mint(tmp_path, "next", "example")
    assert next_step.returncode == 0, next_step.stdout + next_step.stderr
    assert "State: generated output is current" in next_step.stdout


@pytest.mark.e2e
def test_cli_e2e_offline_render_lifecycle(demo_project) -> None:
    root = demo_project.root

    doctor = run_mint(root, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "PASS doctor" in doctor.stdout

    rendered = run_mint(root, "render", "tasklist")
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    assert rendered.stdout.index("RENDER taskstore") < rendered.stdout.index("RENDER tasklist")
    assert "Completed FR2" in rendered.stdout

    metadata_path = root / ".mint" / "generated" / "tasklist" / ".mintgen" / "module.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["lastSuccessfulUnitId"] == "FR2"
    assert all(record["testQuality"]["status"] == "passed" for record in metadata["functionalUnits"])
    assert (root / ".mint" / "generated" / "tasklist" / ".git").is_dir()

    noop = run_mint(root, "render", "tasklist")
    assert noop.returncode == 0, noop.stdout + noop.stderr
    assert "NOOP taskstore" in noop.stdout
    assert "NOOP tasklist" in noop.stdout

    status = run_mint(root, "status", "tasklist")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "Suggested render: no-op" in status.stdout

    report = run_mint(root, "report", "tasklist")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "RUN REPORT tasklist" in report.stdout
    assert "Report JSON: .mint/generated/tasklist/.mintgen/reports/latest.json" in report.stdout

    inspect = run_mint(root, "inspect", "tasklist", "FR1")
    assert inspect.returncode == 0, inspect.stdout + inspect.stderr
    assert "Unit: FR1" in inspect.stdout
    assert "status: passed" in inspect.stdout

    cleaned = run_mint(root, "clean", "tasklist", "--yes")
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    assert not (root / ".mint" / "generated" / "tasklist").exists()


@pytest.mark.e2e
def test_checked_in_launcher_reports_version_outside_repo(tmp_path) -> None:
    result = subprocess.run(
        [str(ROOT / "mint"), "--version"],
        cwd=tmp_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "mint 1.0.0" in result.stdout
