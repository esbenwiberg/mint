from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from mint_cli import workflow
from mint_cli import hashing as handwritten
from mint_cli.errors import MintError
from mint_cli.renderer import patch as handwritten_patch


ROOT = Path(__file__).resolve().parents[1]


def make_self_host_project(make_project, module: str = "mint-hashing"):
    project = make_project()
    shutil.copy(ROOT / ".mint" / "specs" / f"{module}.mint.md", project.spec_path(module))
    cassette_src = ROOT / "resources" / "cassettes"
    cassette_dst = project.root / "resources" / "cassettes"
    cassette_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cassette_src, cassette_dst)
    return project


def test_self_hosted_hashing_replays_and_matches_handwritten(make_project, monkeypatch, tmp_path):
    project = make_self_host_project(make_project)

    status, output = workflow.render_module("mint-hashing", root=project.root)

    assert status == 0, output
    meta = project.metadata("mint-hashing")
    assert meta["provider"] == "model"
    assert meta["model"] == "mint-replay-selfhost-v1"
    assert meta["functionalUnits"][0]["testQuality"]["status"] == "passed"

    monkeypatch.syspath_prepend(str(project.root / ".mint" / "generated" / "mint-hashing" / "src"))
    sys.modules.pop("mint_hashing", None)
    generated = importlib.import_module("mint_hashing")

    value = {"b": 2, "a": ["é"]}
    assert generated.canonical_json(value) == handwritten.canonical_json(value)
    assert generated.hash_json(value) == handwritten.hash_json(value)
    assert generated.hash_text("a\r\nb\n") == handwritten.hash_text("a\r\nb\n")
    assert generated.hash_text("a\r\nb\n") == generated.hash_text("a\nb\n")

    module = tmp_path / "module"
    (module / "src").mkdir(parents=True)
    (module / "src" / "code.py").write_text("print(1)\n", encoding="utf-8")
    (module / ".mintgen").mkdir()
    (module / ".mintgen" / "module.json").write_text("ignored", encoding="utf-8")
    (module / "__pycache__").mkdir()
    (module / "__pycache__" / "code.pyc").write_bytes(b"ignored")

    assert generated.should_skip(Path(".mintgen/module.json")) == handwritten.should_skip(
        Path(".mintgen/module.json")
    )
    assert generated.should_skip(Path("src/code.py")) == handwritten.should_skip(Path("src/code.py"))
    assert generated.hash_generated_files(module) == handwritten.hash_generated_files(module)


# ---- mint-patch: rendered live by claude-fable-5-1, replayed here ----

_VALIDATE_CASES = [
    [1, 2],
    {"summary": "x", "files": []},
    {"summary": "x"},
    {"files": "nope"},
    {"files": [42]},
    {"files": [{"action": "write", "contents": "x"}]},
    {"files": [{"path": "   ", "contents": "x"}]},
    {"files": [{"path": "a.py", "action": "chmod", "contents": "x"}]},
    {"files": [{"path": "a.py", "root": "tests", "contents": "x"}]},
    {"files": [{"path": "a.py", "action": "write"}]},
    {"files": [{"path": "a.py", "contents": 7}]},
    {"files": [{"path": "/usr/bin/evil.py", "contents": "x"}]},
    {"files": [{"path": "../../evil.py", "contents": "x"}]},
    {"files": [{"path": "src/../../evil.py", "contents": "x"}]},
    {"files": [{"path": ".", "contents": "x"}]},
    {"files": [{"path": ".git/hooks/pre-commit", "contents": "x"}]},
    {"files": [{"path": "ok.py", "contents": "x"}, {"path": "src/.git/config", "contents": "x"}]},
    {"files": [{"path": ".mintgen/state.json", "contents": "x"}]},
    {"files": [{"path": "a.py", "contents": "x"}, {"path": "b.py", "action": "delete", "contents": "ignored"}]},
    {"summary": 12, "files": [{"path": "FR1/test_fr1.py", "root": "conformance", "contents": ""}]},
    {"files": [{"path": "src/pkg/module.py", "action": "write", "root": "module", "contents": "pass\n"}]},
]


def _outcome(fn, error_type, *args):
    try:
        return "ok", fn(*args)
    except error_type as exc:
        return "error", str(exc)


@pytest.fixture(scope="module")
def rendered_patch_project(tmp_path_factory: pytest.TempPathFactory):
    """Replay the Fable-recorded mint-patch render once for every parity test."""
    from conftest import _project_skeleton

    def _make(provider="local", model="deterministic-v0", prompt_version="v0"):
        return _project_skeleton(
            tmp_path_factory.mktemp("selfhost-patch") / "proj",
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )

    project = make_self_host_project(_make, "mint-patch")
    status, output = workflow.render_module("mint-patch", root=project.root)
    assert status == 0, output
    return project


@pytest.fixture
def generated_patch(rendered_patch_project, monkeypatch):
    src = rendered_patch_project.root / ".mint" / "generated" / "mint-patch" / "src"
    monkeypatch.syspath_prepend(str(src))
    sys.modules.pop("mint_patch", None)
    return importlib.import_module("mint_patch")


def test_self_hosted_patch_replays_within_retry_budget(rendered_patch_project):
    meta = rendered_patch_project.metadata("mint-patch")
    assert meta["provider"] == "claude-cli"
    assert meta["model"] == "claude-fable-5-1"
    assert [unit["status"] for unit in meta["functionalUnits"]] == ["passed"] * 3
    # Recorded run: FR1 and FR3 first-shot; FR2 needed one retry (its own unit
    # test asserted a message its implementation did not emit).
    assert [unit["attempts"]["unit"] for unit in meta["functionalUnits"]] == [1, 2, 1]
    for unit in meta["functionalUnits"]:
        assert unit["attempts"]["conformance"] == 1, unit
        assert unit["testQuality"]["status"] == "passed", unit


@pytest.mark.parametrize("raw", _VALIDATE_CASES, ids=range(len(_VALIDATE_CASES)))
def test_self_hosted_validate_patch_matches_handwritten(generated_patch, raw):
    expected = _outcome(handwritten_patch.validate_patch, MintError, raw)
    actual = _outcome(generated_patch.validate_patch, generated_patch.PatchError, raw)
    assert actual[0] == expected[0], (expected, actual)
    if expected[0] == "ok":
        assert actual[1] == expected[1]
    else:
        # Same entry is blamed; wording may differ, the index must not.
        for token in ("files[0]", "files[1]"):
            assert (token in actual[1]) == (token in expected[1]), (expected, actual)


def test_self_hosted_constants_match_handwritten(generated_patch):
    assert generated_patch.VALID_ACTIONS == handwritten_patch.VALID_ACTIONS
    assert generated_patch.VALID_ROOTS == handwritten_patch.VALID_ROOTS
    assert generated_patch.RESERVED_FIRST_COMPONENTS == handwritten_patch.RESERVED_FIRST_COMPONENTS


def _twin_roots(tmp_path: Path, name: str) -> tuple[Path, Path]:
    module = tmp_path / name / "module"
    conf = tmp_path / name / "conformance"
    module.mkdir(parents=True)
    conf.mkdir(parents=True)
    return module, conf


def _snapshot(root: Path) -> dict[str, str]:
    """Tree contents keyed by relative path; symlink targets are relativized
    to the tree's parent so the hand/gen twins compare equal."""
    out: dict[str, str] = {}
    anchor = root.parent
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = Path(os.readlink(path))
            try:
                target = target.relative_to(anchor)
            except ValueError:
                pass
            out[rel] = f"symlink->{target.as_posix()}"
        elif path.is_dir():
            out[rel] = "dir"
        else:
            out[rel] = path.read_text(encoding="utf-8")
    return out


def _apply_both(generated_patch, tmp_path: Path, raw: dict, setup=None):
    results = []
    for label, mod, error_type in (
        ("hand", handwritten_patch, MintError),
        ("gen", generated_patch, generated_patch.PatchError),
    ):
        module, conf = _twin_roots(tmp_path, label)
        if setup is not None:
            setup(module, conf)
        patch = handwritten_patch.validate_patch(raw)
        outcome = _outcome(mod.apply_patch, error_type, patch, module, conf)
        results.append((outcome, _snapshot(module), _snapshot(conf)))
    return results


def _assert_twins(results):
    (hand_outcome, hand_mod, hand_conf), (gen_outcome, gen_mod, gen_conf) = results
    assert gen_outcome[0] == hand_outcome[0], (hand_outcome, gen_outcome)
    if hand_outcome[0] == "ok":
        assert gen_outcome[1] == hand_outcome[1]
    assert gen_mod == hand_mod
    assert gen_conf == hand_conf


def test_self_hosted_apply_writes_and_deletes_like_handwritten(generated_patch, tmp_path):
    raw = {
        "files": [
            {"path": "src/x.py", "contents": "print(1)\n"},
            {"path": "FR1/test_fr1.py", "root": "conformance", "contents": "def test(): pass\n"},
        ]
    }
    _assert_twins(_apply_both(generated_patch, tmp_path / "w", raw))

    def seed(module: Path, conf: Path) -> None:
        (module / "src").mkdir()
        (module / "src" / "x.py").write_text("print(1)\n", encoding="utf-8")

    delete = {"files": [{"path": "src/x.py", "action": "delete"}, {"path": "missing.py", "action": "delete"}]}
    _assert_twins(_apply_both(generated_patch, tmp_path / "d", delete, seed))


def test_self_hosted_apply_blocks_symlink_escape_before_any_write(generated_patch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    def seed(module: Path, conf: Path) -> None:
        (module / "link").symlink_to(outside, target_is_directory=True)

    raw = {"files": [{"path": "ok.py", "contents": "x"}, {"path": "link/evil.py", "contents": "x"}]}
    results = _apply_both(generated_patch, tmp_path / "s", raw, seed)
    _assert_twins(results)
    assert results[1][0][0] == "error"
    assert not (outside / "evil.py").exists()
    assert "ok.py" not in results[1][1]


def test_self_hosted_apply_rejects_root_itself(generated_patch, tmp_path):
    def seed(module: Path, conf: Path) -> None:
        (module / "self").symlink_to(module, target_is_directory=True)

    raw = {"files": [{"path": "self", "action": "delete"}]}
    results = _apply_both(generated_patch, tmp_path / "r", raw, seed)
    _assert_twins(results)
    assert results[1][0][0] == "error"


def test_self_hosted_apply_rejects_delete_of_escaping_symlink(generated_patch, tmp_path):
    """The case a second live render got wrong before FR3 acceptance pinned it:
    delete containment is checked on the resolved target, not the link's parent."""
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")

    def seed(module: Path, conf: Path) -> None:
        (module / "out").symlink_to(victim)

    raw = {"files": [{"path": "out", "action": "delete"}]}
    results = _apply_both(generated_patch, tmp_path / "e", raw, seed)
    _assert_twins(results)
    assert results[1][0][0] == "error"
    assert "escapes module dir" in results[1][0][1]
    assert "out" in results[1][1]
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_self_hosted_apply_deletes_dangling_symlink_and_refuses_dirs(generated_patch, tmp_path):
    def seed(module: Path, conf: Path) -> None:
        (module / "dangling").symlink_to(module / "nowhere")
        (module / "pkg").mkdir()

    dangling = {"files": [{"path": "dangling", "action": "delete"}]}
    results = _apply_both(generated_patch, tmp_path / "dl", dangling, seed)
    _assert_twins(results)
    assert results[1][0][0] == "ok"

    directory = {"files": [{"path": "pkg", "action": "delete"}]}
    results = _apply_both(generated_patch, tmp_path / "dd", directory, seed)
    _assert_twins(results)
    assert results[1][0][0] == "error"
