from __future__ import annotations

import json
import shlex
import sys

import pytest

from mint_cli.errors import MintError
from mint_cli.renderer import (
    DeterministicRenderer,
    CliModelClient,
    ModelOutputError,
    ModelRenderer,
    RecordingClient,
    ReplayClient,
    ScriptedModelClient,
    apply_patch,
    build_prompt,
    cassette_model,
    cassette_id,
    extract_json,
    get_renderer,
    normalize_feedback,
    validate_patch,
)
from mint_cli.hashing import hash_text
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
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (module / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform/filesystem specific
        pytest.skip(f"symlinks are unavailable: {exc}")

    patch = validate_patch(
        {
            "files": [
                {
                    "path": "link/evil.py",
                    "action": "write",
                    "contents": "x",
                    "root": "module",
                }
            ]
        }
    )

    with pytest.raises(MintError, match="escapes module dir"):
        apply_patch(patch, module, tmp_path / "c")
    assert not (outside / "evil.py").exists()


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


def test_model_renderer_bubbles_patch_schema_error_with_audit_context():
    client = ScriptedModelClient({"default": "[]"})
    with pytest.raises(ModelOutputError, match="invalid patch") as exc:
        ModelRenderer(client, "v1").render(make_request())

    assert exc.value.prompt
    assert exc.value.response == "[]"
    assert exc.value.cassette_id


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


def test_build_prompt_includes_own_module_files_so_far():
    prompt = build_prompt(
        make_request(
            module_files_so_far=[
                {
                    "path": "src/taskstore/store.py",
                    "contents": "class TaskStore:\n    pass\n",
                    "language": "python",
                }
            ]
        ),
        "v1",
    )
    assert "## Current module files (already rendered for earlier units)" in prompt
    assert "#### src/taskstore/store.py" in prompt
    assert "class TaskStore:" in prompt
    assert "reuse its exact public names" in prompt


def test_build_prompt_omits_own_module_section_when_empty():
    prompt = build_prompt(make_request(), "v1")
    assert "Current module files" not in prompt


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
    assert (
        get_renderer("model", model="m", prompt_version="v", model_client=client).name
        == "model"
    )
    assert (
        get_renderer(
            "claude-cli", model="sonnet", prompt_version="v", model_client=client
        ).name
        == "model"
    )
    assert (
        get_renderer(
            "codex-cli", model="gpt-5-codex", prompt_version="v", model_client=client
        ).name
        == "model"
    )
    with pytest.raises(MintError, match="Unknown renderer provider"):
        get_renderer("banana", model="m", prompt_version="v")


def test_cli_model_client_reads_stdout_from_command(tmp_path):
    script = tmp_path / "fake_model.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert '# System instructions' in prompt\n"
        "assert '# Render request' in prompt\n"
        "print(json.dumps({'summary': 'ok', 'files': "
        "[{'path': 'a.py', 'action': 'write', 'contents': 'x'}]}))\n",
        encoding="utf-8",
    )
    client = CliModelClient(
        name="Fake CLI",
        command=[sys.executable, str(script)],
        timeout_seconds=30,
    )

    response = client.complete(system="system", prompt="prompt", request=make_request())

    assert json.loads(response)["summary"] == "ok"


def test_cli_model_provider_records_with_command_override(tmp_path, monkeypatch):
    script = tmp_path / "fake_model.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'summary': 'ok', 'files': "
        "[{'path': 'a.py', 'action': 'write', 'contents': 'x'}]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINT_LIVE", "1")
    monkeypatch.setenv(
        "MINT_CLAUDE_CLI_COMMAND",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
    )
    renderer = get_renderer(
        "claude-cli",
        model="sonnet",
        prompt_version="pv1",
        cassette_dir=tmp_path,
    )

    outcome = renderer.render(make_request())

    assert outcome.patch["summary"] == "ok"
    assert outcome.cassette_id is not None
    cassette = json.loads((tmp_path / "v1" / f"{outcome.cassette_id}.json").read_text())
    assert cassette["model"] == "claude-cli:sonnet"


def test_cli_model_provider_missing_executable_fails_clearly(monkeypatch):
    monkeypatch.setenv("MINT_CLI_MODEL_TIMEOUT_SECONDS", "30")
    client = CliModelClient(
        name="Fake CLI",
        command=["definitely-not-a-mint-model-cli"],
    )

    with pytest.raises(MintError, match="executable not found"):
        client.complete(system="system", prompt="prompt", request=make_request())


def test_cassette_model_scopes_cli_providers():
    assert cassette_model("model", "sonnet") == "sonnet"
    assert cassette_model("anthropic", "sonnet") == "sonnet"
    assert cassette_model("claude-cli", "sonnet") == "claude-cli:sonnet"
    assert cassette_model("codex-cli", "gpt-5-codex") == "codex-cli:gpt-5-codex"


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
    key = cassette_id(
        prompt_version="pv1",
        request=request,
        prompt="prompt text",
        model="claude-test",
    )
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


def test_recording_clients_for_different_models_do_not_overwrite(tmp_path):
    request = make_request()
    response = json.dumps(
        {"summary": "ok", "files": [{"path": "a.py", "action": "write", "contents": "x"}]}
    )

    RecordingClient(
        ScriptedModelClient({"default": response}),
        cassette_dir=tmp_path,
        model="claude-a",
        prompt_version="pv1",
    ).complete(system="system text", prompt="prompt text", request=request)
    RecordingClient(
        ScriptedModelClient({"default": response}),
        cassette_dir=tmp_path,
        model="claude-b",
        prompt_version="pv1",
    ).complete(system="system text", prompt="prompt text", request=request)

    cassettes = sorted((tmp_path / "v1").glob("*.json"))
    assert len(cassettes) == 2
    assert ReplayClient(
        cassette_dir=tmp_path, model="claude-a", prompt_version="pv1"
    ).complete(system="system text", prompt="prompt text", request=request) == response
    assert ReplayClient(
        cassette_dir=tmp_path, model="claude-b", prompt_version="pv1"
    ).complete(system="system text", prompt="prompt text", request=request) == response


def test_replay_client_accepts_legacy_model_unscoped_cassette(tmp_path):
    request = make_request()
    response = json.dumps(
        {"summary": "ok", "files": [{"path": "a.py", "action": "write", "contents": "x"}]}
    )
    key = cassette_id(prompt_version="pv1", request=request, prompt="prompt text")
    path = tmp_path / "v1" / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "cassetteVersion": 1,
                "id": key,
                "createdAt": "2026-01-01T00:00:00Z",
                "model": "claude-test",
                "promptVersion": "pv1",
                "request": {
                    "module": "taskstore",
                    "unit": "FR1",
                    "phase": "unit",
                    "attempt": 1,
                    "promptHash": "86f704a3919a173d7a7020a33a62e7aa2614effb8612f2d4aad41812fae61472",
                },
                "system": "system text",
                "prompt": "prompt text",
                "response": response,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        ReplayClient(cassette_dir=tmp_path, model="claude-test", prompt_version="pv1").complete(
            system="system text", prompt="prompt text", request=request
        )
        == response
    )


def test_replay_client_fails_loudly_when_prompt_changes(tmp_path):
    request = make_request()
    RecordingClient(
        ScriptedModelClient({"default": '{"files": []}'}),
        cassette_dir=tmp_path,
        model="claude-test",
        prompt_version="pv1",
    ).complete(system="system text", prompt="old prompt", request=request)

    replay = ReplayClient(cassette_dir=tmp_path, model="claude-test", prompt_version="pv1")

    with pytest.raises(MintError) as exc:
        replay.complete(system="system text", prompt="new prompt", request=request)

    message = str(exc.value)
    assert "prompt content changed" in message
    assert "Spec or prompt edits require live recording" in message
    assert "MINT_LIVE=1 mint render taskstore" in message
    assert "MINT_LIVE=1 mint live-smoke taskstore" in message


def test_replay_client_accepts_equivalent_prompt_when_key_drifted(tmp_path):
    request = make_request()
    cassette_dir = tmp_path / "v1"
    cassette_dir.mkdir(parents=True)

    stale_id = "0" * 64
    (cassette_dir / f"{stale_id}.json").write_text(
        json.dumps(
            {
                "cassetteVersion": 1,
                "id": stale_id,
                "createdAt": "2026-01-01T00:00:00Z",
                "model": "claude-test",
                "promptVersion": "pv1",
                "request": {
                    "module": "taskstore",
                    "unit": "FR1",
                    "phase": "unit",
                    "attempt": 1,
                    "promptHash": hash_text("old prompt"),
                },
                "system": "system text",
                "prompt": "old prompt",
                "response": "old response",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    equivalent_id = "1" * 64
    (cassette_dir / f"{equivalent_id}.json").write_text(
        json.dumps(
            {
                "cassetteVersion": 1,
                "id": equivalent_id,
                "createdAt": "2026-01-01T00:00:00Z",
                "model": "claude-test",
                "promptVersion": "pv1",
                "request": {
                    "module": "taskstore",
                    "unit": "FR1",
                    "phase": "unit",
                    "attempt": 1,
                    "promptHash": hash_text("new prompt"),
                },
                "system": "system text",
                "prompt": "new prompt",
                "response": "new response",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    replay = ReplayClient(cassette_dir=tmp_path, model="claude-test", prompt_version="pv1")

    assert replay.complete(system="system text", prompt="new prompt", request=request) == "new response"


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
    assert cassette_id(
        prompt_version="pv1", request=request, prompt="prompt text", model="claude-new"
    ) != base


def test_model_provider_defaults_to_replay_not_live(tmp_path):
    renderer = get_renderer(
        "model",
        model="claude-test",
        prompt_version="pv1",
        cassette_dir=tmp_path,
    )

    with pytest.raises(MintError, match="Replay cassette not found.*MINT_LIVE=1"):
        renderer.render(make_request())


# ---- feedback normalization (deterministic replay) ----


def test_normalize_feedback_strips_nondeterminism():
    raw = (
        "platform darwin -- Python 3.12.0, pytest-8.0.0, pluggy-1.4.0\n"
        "rootdir: /private/tmp/pytest-of-ewi/pytest-3/test_x0\n"
        "plugins: anyio-4.0.0\n"
        "FAILED tests/test_fr1.py::test_add - AssertionError: boom\n"
        "=== 1 failed in 0.03s ===\n"
    )
    out = normalize_feedback(raw)

    assert "Python 3.12" not in out       # platform header dropped
    assert "rootdir" not in out           # rootdir header dropped
    assert "anyio" not in out             # plugins header dropped
    assert "/private/tmp" not in out      # absolute path redacted
    assert "0.03s" not in out             # duration scrubbed
    assert "<DURATION>" in out
    assert "AssertionError: boom" in out  # the actual signal survives


def test_normalize_feedback_strips_addresses_workers_and_timestamps():
    # Beyond the pytest/vitest headers, these three noise sources also vary run to
    # run and would otherwise break replay: object memory addresses, xdist worker
    # ids, and log timestamps.
    raw = (
        "obj <Foo object at 0x104f3a2b0>\n"
        "[gw3] PASSED tests/test_fr1.py::test_add\n"
        "2026-07-02 10:33:01,123 checkpoint written\n"
    )
    out = normalize_feedback(raw)
    assert "0x104f3a2b0" not in out and "<ADDR>" in out
    assert "[gw3]" not in out and "[gw]" in out
    assert "10:33:01" not in out and "<TIMESTAMP>" in out


def test_cassette_id_stable_across_addresses_workers_and_timestamps():
    fb1 = "at 0x104f3a2b0\n[gw3] boom\n2026-07-02 10:33:01,123 x"
    fb2 = "at 0x7ffabc123\n[gw7] boom\n2026-07-03 23:59:59,999 x"
    p1 = build_prompt(make_request(attempt=2, feedback=fb1), "v1")
    p2 = build_prompt(make_request(attempt=2, feedback=fb2), "v1")
    assert p1 == p2
    assert cassette_id(
        prompt_version="v1", request=make_request(attempt=2, feedback=fb1), prompt=p1
    ) == cassette_id(
        prompt_version="v1", request=make_request(attempt=2, feedback=fb2), prompt=p2
    )


def test_normalize_feedback_is_idempotent():
    raw = "FAILED at /tmp/x/test.py in 1.20s\n"
    once = normalize_feedback(raw)
    assert normalize_feedback(once) == once


def test_normalize_feedback_leaves_division_expressions_alone():
    # A non-word char must precede the leading slash to redact — "1/2" is not a path.
    out = normalize_feedback("assert 1/2 == 0.5")
    assert out == "assert 1/2 == 0.5"


def test_cassette_id_stable_across_nondeterministic_feedback():
    # The critical fix: retry prompts embed test output; normalizing it keeps the
    # cassette id stable so a recorded retry can actually replay on another machine.
    fb1 = "rootdir: /tmp/a\nFAILED test_x - boom\n=== 1 failed in 0.03s ==="
    fb2 = "rootdir: /home/ci/b\nFAILED test_x - boom\n=== 1 failed in 1.27s ==="

    p1 = build_prompt(make_request(attempt=2, feedback=fb1), "v1")
    p2 = build_prompt(make_request(attempt=2, feedback=fb2), "v1")

    assert p1 == p2
    assert cassette_id(
        prompt_version="v1", request=make_request(attempt=2, feedback=fb1), prompt=p1
    ) == cassette_id(
        prompt_version="v1", request=make_request(attempt=2, feedback=fb2), prompt=p2
    )


def test_build_prompt_feedback_fence_survives_backticks():
    # Test output containing a ``` run must not break out of the fence.
    prompt = build_prompt(make_request(feedback="Traceback:\n```\nassert False\n```"), "v1")
    assert "assert False" in prompt
    assert "````" in prompt  # outer fence widened past the embedded ```


def test_build_prompt_missing_definition_field_fails_loudly():
    with pytest.raises(MintError, match="missing required field 'text'"):
        build_prompt(make_request(definitions=[{"name": "Task"}]), "v1")


# ---- extract_json robustness ----


def test_extract_json_brace_inside_string_value():
    text = 'prefix {"summary": "has a } brace", "files": [{"path": "a"}]} suffix'
    assert extract_json(text)["summary"] == "has a } brace"


def test_extract_json_skips_failing_candidate():
    text = 'noise {not: json} then {"files": [{"path": "a"}]}'
    assert extract_json(text)["files"][0]["path"] == "a"


# ---- patch path hardening ----


def test_validate_patch_rejects_git_dir():
    patch = {"files": [{"path": ".git/hooks/pre-commit", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match=r"\.git"):
        validate_patch(patch)


def test_validate_patch_rejects_git_dir_at_depth():
    patch = {"files": [{"path": "src/.git/config", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match=r"\.git"):
        validate_patch(patch)


def test_validate_patch_rejects_mintgen_dir():
    patch = {"files": [{"path": ".mintgen/state.json", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match=r"\.mintgen"):
        validate_patch(patch)


def test_validate_patch_rejects_dot_path():
    patch = {"files": [{"path": ".", "action": "write", "contents": "x"}]}
    with pytest.raises(MintError, match="empty path"):
        validate_patch(patch)


def test_apply_patch_atomic_pre_pass_blocks_partial_write(tmp_path):
    module = tmp_path / "m"
    module.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (module / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform specific
        pytest.skip(f"symlinks are unavailable: {exc}")

    patch = validate_patch(
        {
            "files": [
                {"path": "good.py", "action": "write", "contents": "ok\n", "root": "module"},
                {"path": "link/evil.py", "action": "write", "contents": "x", "root": "module"},
            ]
        }
    )

    with pytest.raises(MintError, match="escapes module dir"):
        apply_patch(patch, module, tmp_path / "c")

    # Escape is caught in the pre-pass, so the earlier good entry is never written.
    assert not (module / "good.py").exists()
    assert not (outside / "evil.py").exists()


def test_apply_patch_deletes_broken_symlink(tmp_path):
    module = tmp_path / "m"
    module.mkdir()
    link = module / "dangling"
    try:
        link.symlink_to(module / "gone.txt")  # target inside root, does not exist
    except OSError as exc:  # pragma: no cover - platform specific
        pytest.skip(f"symlinks are unavailable: {exc}")

    assert link.is_symlink()
    patch = validate_patch(
        {"files": [{"path": "dangling", "action": "delete", "root": "module"}]}
    )
    apply_patch(patch, module, tmp_path / "c")

    assert not link.is_symlink()  # actually unlinked, not just reported


# ---- env knobs ----


def test_mint_live_unrecognized_value_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("MINT_LIVE", "maybe")
    with pytest.raises(MintError, match="MINT_LIVE"):
        get_renderer("model", model="m", prompt_version="pv1", cassette_dir=tmp_path)


def test_mint_live_truthy_spelling_enables_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("MINT_LIVE", "true")
    # "true" (not just "1") must select the recording path; with no anthropic key /
    # package this surfaces as a MintError from the live client, not a silent replay.
    with pytest.raises(MintError):
        get_renderer(
            "model", model="m", prompt_version="pv1", cassette_dir=tmp_path
        ).render(make_request())


def test_explicit_cassette_dir_preferred_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINT_CASSETTE_DIR", str(tmp_path / "env_dir"))
    monkeypatch.setenv("MINT_LIVE", "1")
    script = tmp_path / "fake_model.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'summary': 'ok', 'files': "
        "[{'path': 'a.py', 'action': 'write', 'contents': 'x'}]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "MINT_CLAUDE_CLI_COMMAND",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
    )
    explicit = tmp_path / "explicit"

    with pytest.warns(UserWarning, match="MINT_CASSETTE_DIR"):
        renderer = get_renderer(
            "claude-cli", model="sonnet", prompt_version="pv1", cassette_dir=explicit
        )
    outcome = renderer.render(make_request())

    # Recorded under the explicit dir, not the env dir.
    assert (explicit / "v1" / f"{outcome.cassette_id}.json").exists()
    assert not (tmp_path / "env_dir").exists()
