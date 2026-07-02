"""Shared test helpers.

Everything here runs fully offline: the deterministic renderer needs no network,
and the model renderer is always driven by a scripted mock client.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_CONFIG = """version: 1
defaultStack: python-cli
specsDir: .mint/specs
generatedDir: .mint/generated
conformanceDir: conformance
scripts:
  unit: test_scripts/run_unit_tests.sh
  conformance: test_scripts/run_conformance_tests.sh
  prepare: test_scripts/prepare_environment.sh
renderer:
  provider: {provider}
  model: {model}
  promptVersion: {prompt_version}
limits:
  unitRetries: 1
  conformanceRetries: 1
  maxFunctionalUnitsPerRender: 20
  maxModelResponseChars: 200000
  maxRenderAttempts: 0
  maxRenderTokensEstimate: 0
testQuality:
  enabled: true
  minCoveragePercent: 60
  mutationProbe: true
  mutationMaxCandidates: 3
"""


@pytest.fixture(autouse=True)
def _isolate_mint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip MINT_* overrides so a developer's exported shell can't corrupt runs.

    An exported ``MINT_LIVE=1`` (or a stray cassette-dir / CLI-command override)
    would otherwise leak into the suite and produce confusing failures. CI sets
    these explicitly per job, but local shells are unpredictable.
    """
    for name in (
        "MINT_LIVE",
        "MINT_CLAUDE_CLI_COMMAND",
        "MINT_CODEX_CLI_COMMAND",
        "MINT_CASSETTE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_spec(self, module: str, text: str) -> Path:
        path = self.spec_path(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def spec_path(self, module: str) -> Path:
        return self.root / ".mint" / "specs" / f"{module}.mint.md"

    def metadata(self, module: str) -> dict:
        import json

        path = self.root / ".mint" / "generated" / module / ".mintgen" / "module.json"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def make_project(tmp_path: Path):
    def _make(provider: str = "local", model: str = "deterministic-v0", prompt_version: str = "v0") -> Project:
        root = tmp_path / "proj"
        root.mkdir()
        (root / "mint.yaml").write_text(
            DEFAULT_CONFIG.format(provider=provider, model=model, prompt_version=prompt_version),
            encoding="utf-8",
        )
        scripts_dst = root / "test_scripts"
        shutil.copytree(REPO_ROOT / "test_scripts", scripts_dst)
        for script in scripts_dst.glob("*.sh"):
            script.chmod(0o755)
        (root / ".mint" / "specs").mkdir(parents=True)
        return Project(root)

    return _make


@pytest.fixture
def demo_project(make_project):
    """A project with the two committed demo specs (taskstore + tasklist)."""
    project = make_project()
    shutil.copy(REPO_ROOT / ".mint" / "specs" / "taskstore.mint.md", project.spec_path("taskstore"))
    shutil.copy(REPO_ROOT / ".mint" / "specs" / "tasklist.mint.md", project.spec_path("tasklist"))
    return project
