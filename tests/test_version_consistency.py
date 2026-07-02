"""Guards against silent drift between duplicated sources of truth.

1. The package version is declared once (the ``VERSION`` file) and surfaced via
   ``mint_cli.__version__`` and the installed distribution metadata. All three
   must agree.
2. The committed ``test_scripts/*.sh`` scaffolds are mirrored as embedded
   ``*_SH`` constants in ``mint_cli.workflow`` (used by ``mint init --write``).
   They must stay byte-identical or the scaffold templates rot silently.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest

import mint_cli
from mint_cli import workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "VERSION"


def test_version_file_matches_package_attribute() -> None:
    file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert mint_cli.__version__ == file_version


def test_version_matches_installed_metadata() -> None:
    try:
        installed = metadata.version("mint-regenerative")
    except metadata.PackageNotFoundError:
        pytest.skip("mint-regenerative is not installed; run pip install -e .[dev]")
    file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert installed == file_version == mint_cli.__version__


SCRIPT_CONSTANTS = {
    "test_scripts/prepare_environment.sh": workflow.PREPARE_ENVIRONMENT_SH,
    "test_scripts/run_unit_tests.sh": workflow.RUN_UNIT_TESTS_SH,
    "test_scripts/run_conformance_tests.sh": workflow.RUN_CONFORMANCE_TESTS_SH,
}


@pytest.mark.parametrize("rel_path", sorted(SCRIPT_CONSTANTS))
def test_committed_scripts_match_workflow_constants(rel_path: str) -> None:
    committed = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert committed == SCRIPT_CONSTANTS[rel_path], (
        f"{rel_path} has drifted from its mint_cli.workflow scaffold constant; "
        "regenerate the committed script or update the constant so `mint init "
        "--write` stays faithful."
    )
