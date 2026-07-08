#!/usr/bin/env python3
"""Regenerate the committed replay cassettes from fixture responses.

The calc-graph and mint-hashing replay cassettes are keyed on a hash of the exact
render prompt, so ANY change to prompt construction (build_prompt, prompt hints,
required-module context, system prompt) makes every committed cassette stale and
fails the replay test suites. This script re-records them deterministically:

  * canned responses live in resources/replay-fixtures/responses.json,
    keyed "module/FRn" with "@variant" suffixes for spec-edit scenarios;
  * each scenario below mirrors a test in tests/test_calc_graph.py /
    tests/test_self_hosting.py exactly (same spec edits, same render calls);
  * a ScriptedModelClient replays the canned responses while a RecordingClient
    writes fresh cassettes straight into resources/cassettes/v1.

Run it after any prompt-affecting change, then run the test suite:

    python scripts/regenerate_replay_cassettes.py
    pytest tests/test_calc_graph.py tests/test_self_hosting.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mint_cli import workflow  # noqa: E402
from mint_cli.renderer import RecordingClient, ScriptedModelClient  # noqa: E402
from mint_cli.renderer.base import RenderRequest  # noqa: E402

CASSETTE_DIR = REPO_ROOT / "resources" / "cassettes"
FIXTURES = REPO_ROOT / "resources" / "replay-fixtures" / "responses.json"
CALC_MODULES = ["lexer", "parser", "evaluator", "calc-cli"]

MINT_YAML = """version: 1
defaultStack: python-cli
specsDir: .mint/specs
generatedDir: .mint/generated
conformanceDir: conformance
scripts:
  unit: test_scripts/run_unit_tests.sh
  conformance: test_scripts/run_conformance_tests.sh
  prepare: test_scripts/prepare_environment.sh
renderer:
  provider: local
  model: deterministic-v0
  promptVersion: v0
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

# Spec edits per scenario — MUST stay in sync with tests/test_calc_graph.py.
EVALUATOR_MSG_EDIT = (
    '`evaluate("missing(1)")` raises `EvalError` with `unknown name` in the message.',
    '`evaluate("missing(1)")` raises `EvalError` with `unknown name` in the clean message.',
)
LEXER_WHITESPACE_EDIT = (
    "Whitespace is ignored.",
    "Whitespace is ignored before and after every token.",
)
LEXER_INTERNAL_EDIT = (
    "Whitespace is ignored.",
    "Whitespace is ignored between tokens.",
)


def load_responses() -> dict[str, str]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def response_key(request: RenderRequest) -> str:
    unit_text = " ".join(
        list(request.current_unit.get("spec", [])) + list(request.current_unit.get("acceptance", []))
    )
    key = f"{request.module}/{request.current_unit_id}"
    if request.module == "lexer" and LEXER_WHITESPACE_EDIT[1] in unit_text:
        key += "@whitespace"
    elif request.module == "lexer" and LEXER_INTERNAL_EDIT[1] in unit_text:
        key += "@internal"
    elif request.module == "evaluator" and EVALUATOR_MSG_EDIT[1] in unit_text:
        key += "@message"
    return key


def make_client(responses: dict[str, str]) -> ScriptedModelClient:
    def respond(request: RenderRequest) -> str:
        if request.phase != "unit" or request.attempt != 1:
            raise SystemExit(
                f"Fixture response for {request.module}/{request.current_unit_id} did not "
                f"pass first try (phase={request.phase}, attempt={request.attempt}). "
                "Fixtures must be known-good; fix the fixture, not the retry."
            )
        key = response_key(request)
        if key not in responses:
            raise SystemExit(f"No fixture response for {key} in {FIXTURES}")
        return responses[key]

    return ScriptedModelClient(respond)


def make_project(root: Path, modules: list[str]) -> Path:
    root.mkdir(parents=True)
    (root / "mint.yaml").write_text(MINT_YAML, encoding="utf-8")
    shutil.copytree(REPO_ROOT / "test_scripts", root / "test_scripts")
    for script in (root / "test_scripts").glob("*.sh"):
        script.chmod(0o755)
    specs = root / ".mint" / "specs"
    specs.mkdir(parents=True)
    for module in modules:
        shutil.copy(REPO_ROOT / ".mint" / "specs" / f"{module}.mint.md", specs / f"{module}.mint.md")
    return root


def edit_spec(root: Path, module: str, old: str, new: str) -> None:
    path = root / ".mint" / "specs" / f"{module}.mint.md"
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Spec edit anchor not found in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def render(root: Path, module: str, client: RecordingClient) -> str:
    status, output = workflow.render_module(module, root=root, model_client=client)
    if status != 0:
        raise SystemExit(f"render {module} failed:\n{output}")
    return output


def calc_recorder(responses: dict[str, str]) -> RecordingClient:
    return RecordingClient(
        make_client(responses),
        cassette_dir=CASSETTE_DIR,
        model="mint-replay-calc-v1",
        prompt_version="calc-v1",
    )


def main() -> int:
    responses = load_responses()
    v1 = CASSETTE_DIR / "v1"
    if v1.exists():
        shutil.rmtree(v1)

    with tempfile.TemporaryDirectory(prefix="mint-cassettes-") as tmp:
        tmp_path = Path(tmp)

        # S1+S2: clean calc render, then the evaluator FR2 message edit + revert
        # (mirrors test_evaluator_later_unit_edit / test_reverting_spec_text).
        root = make_project(tmp_path / "s1", CALC_MODULES)
        render(root, "calc-cli", calc_recorder(responses))
        edit_spec(root, "evaluator", *EVALUATOR_MSG_EDIT)
        render(root, "evaluator", calc_recorder(responses))

        # S3: lexer edit whose generated code changes its PUBLIC INTERFACE
        # (docstring) — cascades through the whole graph
        # (mirrors test_lexer_spec_edit_cascades_through_calc_graph).
        root = make_project(tmp_path / "s3", CALC_MODULES)
        render(root, "calc-cli", calc_recorder(responses))
        edit_spec(root, "lexer", *LEXER_WHITESPACE_EDIT)
        output = render(root, "calc-cli", calc_recorder(responses))
        if output.count("required module code changed") < 3:
            raise SystemExit(
                "expected the whitespace lexer variant to cascade (interface change); "
                "check the fixture docstring.\n" + output
            )

        # S4: lexer edit whose generated code keeps the SAME public interface
        # (comment-only) — dependents must NOOP, proving internal-only changes
        # no longer cascade (mirrors the internal-edit test).
        root = make_project(tmp_path / "s4", CALC_MODULES)
        render(root, "calc-cli", calc_recorder(responses))
        edit_spec(root, "lexer", *LEXER_INTERNAL_EDIT)
        output = render(root, "calc-cli", calc_recorder(responses))
        if "NOOP parser" not in output or "NOOP calc-cli" not in output:
            raise SystemExit(
                "expected the internal lexer variant NOT to cascade (same interface).\n" + output
            )

        # S5: self-hosted hashing module (mirrors tests/test_self_hosting.py).
        root = make_project(tmp_path / "s5", ["mint-hashing"])
        recorder = RecordingClient(
            make_client(responses),
            cassette_dir=CASSETTE_DIR,
            model="mint-replay-selfhost-v1",
            prompt_version="selfhost-v1",
        )
        render(root, "mint-hashing", recorder)

    count = len(list(v1.glob("*.json")))
    print(f"Recorded {count} cassettes into {v1.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
