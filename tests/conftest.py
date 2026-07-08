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
        "MINT_PY_INSTALL_COMMAND",
        "MINT_TS_INSTALL_COMMAND",
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


def _project_skeleton(
    root: Path,
    provider: str = "local",
    model: str = "deterministic-v0",
    prompt_version: str = "v0",
) -> Project:
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


def _copy_demo_specs(project: Project) -> Project:
    shutil.copy(REPO_ROOT / ".mint" / "specs" / "taskstore.mint.md", project.spec_path("taskstore"))
    shutil.copy(REPO_ROOT / ".mint" / "specs" / "tasklist.mint.md", project.spec_path("tasklist"))
    return project


@pytest.fixture
def make_project(tmp_path: Path):
    def _make(provider: str = "local", model: str = "deterministic-v0", prompt_version: str = "v0") -> Project:
        return _project_skeleton(
            tmp_path / "proj", provider=provider, model=model, prompt_version=prompt_version
        )

    return _make


@pytest.fixture
def demo_project(make_project):
    """A project with the two committed demo specs (taskstore + tasklist)."""
    return _copy_demo_specs(make_project())


@pytest.fixture(scope="session")
def _rendered_demo_snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Render the taskstore+tasklist demo graph once per worker.

    A full graph render costs ~25s; a dozen tests only need an already-rendered
    project to assert against. Content hashes are location-independent, so a
    directory copy of this snapshot behaves exactly like a freshly rendered
    project (test_second_render_is_noop proves it against a copy).
    """
    from mint_cli import workflow

    project = _project_skeleton(tmp_path_factory.mktemp("rendered-demo") / "proj")
    _copy_demo_specs(project)
    status, output = workflow.render_module("tasklist", root=project.root)
    assert status == 0, output
    return project.root


@pytest.fixture
def rendered_demo_project(_rendered_demo_snapshot: Path, tmp_path: Path) -> Project:
    """A demo_project whose tasklist graph (taskstore + tasklist) is already rendered.

    Tests that mutate the project get their own copy; the shared snapshot is
    never touched.
    """
    root = tmp_path / "proj"
    shutil.copytree(_rendered_demo_snapshot, root)
    return Project(root)
