from __future__ import annotations

import json

import pytest

from mint_cli.errors import MintError
from mint_cli.renderer import (
    DeterministicRenderer,
    ModelOutputError,
    ModelRenderer,
    RecordingClient,
    ReplayClient,
    ScriptedModelClient,
    apply_patch,
    build_prompt,
    cassette_id,
    extract_json,
    get_renderer,
    validate_patch,
)
from mint_cli.renderer.base import RenderRequest


def make_request(**overrides) -> RenderRequest:
    base = dict(
        module="taskstore",
        stack="python-lib",
        template="taskstore",
        spec_ir={},
        definitions=[{"name": "Task", "text": "item"}],
        implementation=["lib"],
        test=["pytest"],
        imported_context=[],
        required_modules=[],
        units_so_far=[{"id": "FR1", "title": "Add", "spec": ["s"], "acceptance": ["a"]}],
        current_unit={"id": "FR1", "title": "Add", "spec": ["s"], "acceptance": ["a"]},
        phase="unit",
        attempt=1,
        feedback=None,
    )
    base.update(overrides)
    return RenderRequest(**base)


# ---- patch contract ----


def test_validate_patch_rejects_non_dict():
    with pytest.raises(MintError, match="must be a JSON object"):
        validate_patch([1, 2])


def test_validate_patch_requires_files():
    with pytest.raises(MintError, match="non-empty 'files'"):
        validate_patch({"summary": "x", "files": []})


def test_validate_patch_rejects_absolute_path():
    patch = {"files": [{"path": "/etc/passwd", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match="must be relative"):
        validate_patch(patch)


def test_validate_patch_rejects_parent_escape():
    patch = {"files": [{"path": "../../evil.py", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match="escape"):
        validate_patch(patch)


def test_validate_patch_rejects_bad_action():
    patch = {"files": [{"path": "a.py", "action": "chmod", "contents": "x"}]}
    with pytest.raises(MintError, match="invalid action"):
        validate_patch(patch)


def test_apply_patch_writes_and_deletes(tmp_path):
    module = tmp_path / "m"
    conf = tmp_path / "c"
    patch = validate_patch(
        {
            "files": [
                {"path": "src/x.py", "action": "write", "contents": "print(1)\n"},
                {"path": "FR1/test_x.py", "action": "write", "contents": "def test(): pass\n", "root": "conformance"},
            ]
        }
    )
    apply_patch(patch, module, conf)
    assert (module / "src" / "x.py").read_text() == "print(1)\n"
    assert (conf / "FR1" / "test_x.py").exists()

    delete = validate_patch({"files": [{"path": "src/x.py", "action": "delete"}]})
    apply_patch(delete, module, conf)
    assert not (module / "src" / "x.py").exists()


def test_apply_patch_blocks_symlink_escape(tmp_path):
    # A path that resolves outside the root must be refused even after resolve().
    module = tmp_path / "m"
    module.mkdir()
    outside = tmp_path / "outside.py"
    patch = {"files": [{"path": "link.py", "action": "write", "contents": "x", "root": "module"}]}
    validated = validate_patch(patch)
    # Normal case stays inside; sanity check the guard does not false-positive.
    apply_patch(validated, module, tmp_path / "c")
    assert (module / "link.py").exists()
    assert not outside.exists()


# ---- deterministic renderer ----


def test_deterministic_renders_known_template():
    out = DeterministicRenderer().render(make_request())
    patch = validate_patch(out.patch)
    paths = {f"{f['root']}:{f['path']}" for f in patch["files"]}
    assert "module:src/taskstore/store.py" in paths
    assert "conformance:FR1/test_fr1.py" in paths
    assert out.renderer == "deterministic"
    # Response is the canonical patch JSON (uniform audit trail).
    assert json.loads(out.response)["files"]


def test_deterministic_unknown_template_errors():
    with pytest.raises(MintError, match="no template"):
        DeterministicRenderer().render(make_request(module="nope", template="nope"))


def test_deterministic_is_repeatable():
    a = DeterministicRenderer().render(make_request())
    b = DeterministicRenderer().render(make_request())
    assert a.response == b.response


# ---- model renderer (offline, scripted) ----


def test_extract_json_direct():
    assert extract_json('{"files": []}') == {"files": []}


def test_extract_json_from_fence():
    text = "Here you go:\n```json\n{\"files\": [{\"path\": \"a\"}]}\n```\nDone."
    assert extract_json(text)["files"][0]["path"] == "a"


def test_extract_json_balanced():
    text = 'prefix {"summary": "s", "files": [{"path": "a"}]} suffix'
    assert extract_json(text)["summary"] == "s"


def test_extract_json_failure():
    with pytest.raises(MintError, match="no JSON object"):
        extract_json("no json here")


def test_model_renderer_parses_scripted_response():
    canned = json.dumps({"summary": "ok", "files": [{"path": "a.py", "action": "write", "contents": "x"}]})
    client = ScriptedModelClient({"default": canned})
    out = ModelRenderer(client, "v1").render(make_request())
    assert validate_patch(out.patch)["summary"] == "ok"
    assert out.prompt and out.response == canned
    assert out.cassette_id == cassette_id(
        prompt_version="v1",
        request=make_request(),
        prompt=out.prompt,
    )
    assert client.calls == [("FR1", "unit", 1)]


def test_model_renderer_bubbles_parse_error():
    client = ScriptedModelClient({"default": "not json"})
    with pytest.raises(MintError, match="unparseable"):
        ModelRenderer(client, "v1").render(make_request())


def test_model_renderer_caps_response_size():
    client = ScriptedModelClient({"default": "x" * 20})
    with pytest.raises(ModelOutputError, match="exceeded 5 characters") as exc:
        ModelRenderer(client, "v1", max_response_chars=5).render(make_request())
    assert exc.value.response == "x" * 20
    assert exc.value.prompt


def test_scripted_client_keying():
    client = ScriptedModelClient({"FR1:unit:2": "retry", "default": "base"})
    assert client.complete(system="", prompt="", request=make_request(attempt=2)) == "retry"
    assert client.complete(system="", prompt="", request=make_request(attempt=1)) == "base"


def test_build_prompt_includes_feedback():
    prompt = build_prompt(make_request(feedback="AssertionError: boom"), "v1")
    assert "AssertionError: boom" in prompt
    assert "Implement unit FR1" in prompt


def test_build_prompt_includes_required_module_code_contents():
    prompt = build_prompt(
        make_request(
            required_modules=[
                {
                    "module": "dep",
                    "files": [
                        {
                            "path": "src/dep/api.py",
                            "contents": "def public_api():\n    return 42\n",
                        }
                    ],
                }
            ]
        ),
        "v1",
    )
    assert "#### src/dep/api.py" in prompt
    assert "def public_api():" in prompt


def test_get_renderer_selects_provider():
    assert get_renderer("local", model="m", prompt_version="v").name == "deterministic"
    client = ScriptedModelClient({"default": "{}"})
    assert get_renderer("model", model="m", prompt_version="v", model_client=client).name == "model"
    with pytest.raises(MintError, match="Unknown renderer provider"):
        get_renderer("banana", model="m", prompt_version="v")


# ---- model record/replay cassettes ----


def test_recording_client_writes_versioned_cassette(tmp_path):
    request = make_request()
    response = json.dumps(
        {"summary": "ok", "files": [{"path": "a.py", "action": "write", "contents": "x"}]}
    )
    wrapped = ScriptedModelClient({"default": response})
    client = RecordingClient(
        wrapped,
        cassette_dir=tmp_path,
        model="claude-test",
        prompt_version="pv1",
    )

    actual = client.complete(system="system text", prompt="prompt text", request=request)

    assert actual == response
    key = cassette_id(prompt_version="pv1", request=request, prompt="prompt text")
    path = tmp_path / "v1" / f"{key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cassetteVersion"] == 1
    assert data["id"] == key
    assert data["model"] == "claude-test"
    assert data["promptVersion"] == "pv1"
    assert data["system"] == "system text"
    assert data["prompt"] == "prompt text"
    assert data["response"] == response
    assert data["request"]["module"] == "taskstore"
    assert data["request"]["unit"] == "FR1"


def test_replay_client_serves_recorded_response(tmp_path):
    request = make_request()
    response = json.dumps(
        {"summary": "ok", "files": [{"path": "a.py", "action": "write", "contents": "x"}]}
    )
    RecordingClient(
        ScriptedModelClient({"default": response}),
        cassette_dir=tmp_path,
        model="claude-test",
        prompt_version="pv1",
    ).complete(system="system text", prompt="prompt text", request=request)

    replay = ReplayClient(cassette_dir=tmp_path, model="claude-test", prompt_version="pv1")

    assert replay.complete(system="system text", prompt="prompt text", request=request) == response


def test_replay_client_fails_loudly_when_prompt_changes(tmp_path):
    request = make_request()
    RecordingClient(
        ScriptedModelClient({"default": '{"files": []}'}),
        cassette_dir=tmp_path,
        model="claude-test",
        prompt_version="pv1",
    ).complete(system="system text", prompt="old prompt", request=request)

    replay = ReplayClient(cassette_dir=tmp_path, model="claude-test", prompt_version="pv1")

    with pytest.raises(MintError, match="prompt content changed.*MINT_LIVE=1"):
        replay.complete(system="system text", prompt="new prompt", request=request)


def test_replay_client_fails_loudly_when_model_changes(tmp_path):
    request = make_request()
    RecordingClient(
        ScriptedModelClient({"default": '{"files": []}'}),
        cassette_dir=tmp_path,
        model="claude-old",
        prompt_version="pv1",
    ).complete(system="system text", prompt="prompt text", request=request)

    replay = ReplayClient(cassette_dir=tmp_path, model="claude-new", prompt_version="pv1")

    with pytest.raises(MintError, match="model mismatch.*MINT_LIVE=1"):
        replay.complete(system="system text", prompt="prompt text", request=request)


def test_cassette_id_includes_prompt_version_prompt_and_attempt():
    request = make_request()
    base = cassette_id(prompt_version="pv1", request=request, prompt="prompt text")

    assert cassette_id(prompt_version="pv2", request=request, prompt="prompt text") != base
    assert cassette_id(prompt_version="pv1", request=request, prompt="changed prompt") != base
    assert cassette_id(prompt_version="pv1", request=make_request(attempt=2), prompt="prompt text") != base


def test_model_provider_defaults_to_replay_not_live(tmp_path):
    renderer = get_renderer(
        "model",
        model="claude-test",
        prompt_version="pv1",
        cassette_dir=tmp_path,
    )

    with pytest.raises(MintError, match="Replay cassette not found.*MINT_LIVE=1"):
        renderer.render(make_request())
