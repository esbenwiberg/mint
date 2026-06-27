from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_mint(*args: str, cwd=ROOT, env_update=None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONPYCACHEPREFIX"] = str(ROOT / ".pytest_cache" / "pycache")
    for key, value in (env_update or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-m", "mint_cli", *args],
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# pytest-style CLI integration tests against an isolated demo project.


def test_cli_render_status_inspect_clean_roundtrip(demo_project) -> None:
    root = demo_project.root

    rendered = run_mint("render", "tasklist", cwd=root)
    assert rendered.returncode == 0, rendered.stderr
    assert "RENDER taskstore" in rendered.stdout
    assert "RENDER tasklist" in rendered.stdout

    noop = run_mint("render", "tasklist", cwd=root)
    assert "NOOP tasklist" in noop.stdout

    status = run_mint("status", "tasklist", cwd=root)
    assert status.returncode == 0
    assert "Suggested render: no-op" in status.stdout

    inspect = run_mint("inspect", "tasklist", "FR1", cwd=root)
    assert inspect.returncode == 0
    assert "Unit: FR1" in inspect.stdout

    healthcheck = run_mint("healthcheck", "tasklist", cwd=root)
    assert healthcheck.returncode == 0
    assert "PASS tasklist" in healthcheck.stdout

    report = run_mint("report", "tasklist", cwd=root)
    assert report.returncode == 0
    assert "RUN REPORT tasklist" in report.stdout

    cleaned = run_mint("clean", "tasklist", "--yes", cwd=root)
    assert cleaned.returncode == 0
    assert not (root / "generated" / "tasklist").exists()


def test_cli_new_lint_and_doctor(demo_project) -> None:
    root = demo_project.root

    created = run_mint("new", "scratch", cwd=root)
    assert created.returncode == 0, created.stderr
    assert "specs/scratch.mint.md" in created.stdout

    linted = run_mint("lint", "scratch", cwd=root)
    assert linted.returncode == 0, linted.stdout + linted.stderr
    assert "PASS lint scratch" in linted.stdout

    doctor = run_mint("doctor", cwd=root)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "PASS doctor" in doctor.stdout


def test_cli_unknown_unit_reports_error(demo_project) -> None:
    run_mint("render", "taskstore", cwd=demo_project.root)
    result = run_mint("inspect", "taskstore", "FR99", cwd=demo_project.root)
    assert result.returncode == 1
    assert "Unknown functional unit" in result.stderr


def test_cli_live_smoke_is_env_gated(make_project) -> None:
    project = make_project()
    shutil.copy(ROOT / "specs" / "mint-hashing.mint.md", project.spec_path("mint-hashing"))

    offline = run_mint(
        "live-smoke",
        "mint-hashing",
        cwd=project.root,
        env_update={"MINT_LIVE": None, "ANTHROPIC_API_KEY": None},
    )

    assert offline.returncode == 1
    assert "FAIL live-smoke mint-hashing" in offline.stdout
    assert "MINT_LIVE=1 mint live-smoke mint-hashing" in offline.stdout

    missing_key = run_mint(
        "live-smoke",
        "mint-hashing",
        cwd=project.root,
        env_update={"MINT_LIVE": "1", "ANTHROPIC_API_KEY": None},
    )

    assert missing_key.returncode == 1
    assert "ANTHROPIC_API_KEY is not set" in missing_key.stdout


class CliTest(unittest.TestCase):
    def test_version_reports_v1(self) -> None:
        result = run_mint("--version")

        self.assertEqual(result.returncode, 0)
        self.assertIn("1.0.0", result.stdout)

    def test_help_lists_phase_zero_commands(self) -> None:
        result = run_mint("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Local Codeplain-inspired regenerative coding workflow.", result.stdout)
        for command in [
            "init",
            "parse",
            "new",
            "lint",
            "doctor",
            "healthcheck",
            "render",
            "live-smoke",
            "status",
            "report",
            "inspect",
            "clean",
        ]:
            self.assertIn(command, result.stdout)

    def test_init_reports_seed_files(self) -> None:
        result = run_mint("init")

        self.assertEqual(result.returncode, 0)
        self.assertIn("mint Phase 0 skeleton", result.stdout)
        self.assertIn("specs/example.mint.md", result.stdout)

    def test_parse_emits_canonical_ir(self) -> None:
        result = run_mint("parse", "example")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"module": "example"', result.stdout)
        self.assertIn('"id": "FR1"', result.stdout)
        self.assertIn('"id": "FR2"', result.stdout)

    def test_healthcheck_passes_for_seed_module(self) -> None:
        result = run_mint("healthcheck", "example")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS example", result.stdout)
        self.assertIn("Spec parsed: 2 functional units", result.stdout)


if __name__ == "__main__":
    unittest.main()
