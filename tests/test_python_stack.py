"""Python stack adapter: prompt hints, interface stubs, and dependency install."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from mint_cli.stacks import (
    PythonStackAdapter,
    python_interface_stub,
    script_env,
)


ADAPTER = PythonStackAdapter()


def make_context(tmp_path: Path, module: str = "demo") -> SimpleNamespace:
    generated = tmp_path / "generated" / module
    generated.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        module=module,
        generated_dir=generated,
        conformance_dir=tmp_path / "conformance" / module,
    )


# ---- prompt hints ----


def test_python_prompt_hints_carry_harness_conventions():
    hints = ADAPTER.prompt_hints(None, ())
    text = " ".join(hints)
    assert "FR1/test_fr1.py" in text
    assert "tests/" in text and "test_*.py" in text
    assert "current unit only" in text.lower() or "CURRENT unit only" in text
    assert "pyproject.toml" in text


def test_python_prompt_hints_name_required_modules_as_stubs():
    hints = ADAPTER.prompt_hints(None, ("lexer", "parser"))
    text = " ".join(hints)
    assert "lexer, parser" in text
    assert "public interface stubs" in text
    # Without required modules the stub hint is absent.
    assert "public interface stubs" not in " ".join(ADAPTER.prompt_hints(None, ()))


# ---- interface stubs ----


def test_interface_stub_keeps_public_surface_only():
    source = '''"""Module doc."""
from dataclasses import dataclass

_PRIVATE_CONST = 1
PUBLIC_CONST = ["a", "b"]
DERIVED = compute_something()

@dataclass(frozen=True)
class Token:
    """A token."""
    type: str
    value: str | None = None

    def pretty(self) -> str:
        return f"{self.type}"

    def _hidden(self) -> None:
        pass

def tokenize(text: str) -> list[Token]:
    """Tokenize text."""
    return []

def _helper() -> None:
    pass
'''
    stub = python_interface_stub(source)
    assert '"""Module doc."""' in stub
    assert "from dataclasses import dataclass" in stub
    assert "PUBLIC_CONST = ['a', 'b']" in stub
    assert "DERIVED = ..." in stub
    assert "_PRIVATE_CONST" not in stub
    assert "def tokenize(text: str) -> list[Token]:" in stub
    assert '"""Tokenize text."""' in stub
    assert "def pretty(self) -> str:" in stub
    assert "_hidden" not in stub
    assert "_helper" not in stub
    assert "return []" not in stub  # bodies are stubbed
    assert "return f" not in stub


def test_interface_stub_stable_under_internal_edits():
    source = "def api(x: int) -> int:\n    return x + 1\n"
    edited = "def api(x: int) -> int:\n    # refactored\n    y = x\n    return y + 1\n"
    assert python_interface_stub(source) == python_interface_stub(edited)


def test_interface_stub_changes_when_signature_or_docstring_changes():
    base = "def api(x: int) -> int:\n    return x\n"
    new_sig = "def api(x: int, y: int = 0) -> int:\n    return x\n"
    new_doc = 'def api(x: int) -> int:\n    """Now documented."""\n    return x\n'
    assert python_interface_stub(base) != python_interface_stub(new_sig)
    assert python_interface_stub(base) != python_interface_stub(new_doc)


def test_interface_stub_falls_back_to_source_on_syntax_error():
    broken = "def broken(:\n"
    assert python_interface_stub(broken) == broken


def test_required_context_files_skips_private_modules(tmp_path):
    module_dir = tmp_path / "mod"
    src = module_dir / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("from .api import go\n", encoding="utf-8")
    (src / "api.py").write_text("def go() -> int:\n    return 1\n", encoding="utf-8")
    (src / "_mint_provenance.py").write_text("PROVENANCE = {}\n", encoding="utf-8")

    files = ADAPTER.required_context_files(module_dir)

    paths = [item["path"] for item in files]
    assert "src/pkg/__init__.py" in paths
    assert "src/pkg/api.py" in paths
    assert not any("_mint_provenance" in path for path in paths)
    api = next(item for item in files if item["path"] == "src/pkg/api.py")
    assert "def go() -> int:" in api["contents"]
    assert "return 1" not in api["contents"]


# ---- dependency install ----


def write_pyproject(context: SimpleNamespace, dependencies: list[str]) -> None:
    deps = ", ".join(f'"{dep}"' for dep in dependencies)
    (context.generated_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{context.module}"\nversion = "0.1.0"\ndependencies = [{deps}]\n',
        encoding="utf-8",
    )


def test_no_install_when_no_pyproject(tmp_path):
    context = make_context(tmp_path)
    assert ADAPTER._ensure_dependencies_installed(context, ()) is None


def test_no_install_when_only_local_required_modules(tmp_path, monkeypatch):
    context = make_context(tmp_path, module="tasklist")
    write_pyproject(context, ["taskstore"])
    # Any pip invocation would fail loudly through this override.
    monkeypatch.setenv("MINT_PY_INSTALL_COMMAND", "false")
    assert ADAPTER._ensure_dependencies_installed(context, ("taskstore",)) is None
    assert not (context.generated_dir / ".mint-deps").exists()


def test_install_runs_override_and_writes_marker(tmp_path, monkeypatch):
    context = make_context(tmp_path)
    write_pyproject(context, ["fakedep>=1.0"])
    log = tmp_path / "install.log"
    script = tmp_path / "fake-install.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$MINT_PY_DEPS_REQUIREMENTS" > "{log}"\n'
        'mkdir -p "$MINT_PY_DEPS_TARGET"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MINT_PY_INSTALL_COMMAND", str(script))

    assert ADAPTER._ensure_dependencies_installed(context, ()) is None

    assert log.read_text(encoding="utf-8").strip() == "fakedep>=1.0"
    marker = context.generated_dir / ".mint-deps" / ".mint-install.json"
    assert json.loads(marker.read_text(encoding="utf-8")) == ["fakedep>=1.0"]

    # Second call is a no-op: the marker signature matches.
    monkeypatch.setenv("MINT_PY_INSTALL_COMMAND", "false")
    assert ADAPTER._ensure_dependencies_installed(context, ()) is None


def test_install_failure_returns_retryable_result(tmp_path, monkeypatch):
    context = make_context(tmp_path)
    write_pyproject(context, ["fakedep"])
    monkeypatch.setenv("MINT_PY_INSTALL_COMMAND", "false")

    result = ADAPTER._ensure_dependencies_installed(context, ())

    assert result is not None
    assert result.returncode != 0


def test_invalid_pyproject_returns_retryable_result(tmp_path):
    context = make_context(tmp_path)
    (context.generated_dir / "pyproject.toml").write_text("not [valid toml", encoding="utf-8")

    result = ADAPTER._ensure_dependencies_installed(context, ())

    assert result is not None
    assert result.returncode == 1
    assert "invalid TOML" in result.stderr


def test_install_dir_is_git_excluded(tmp_path, monkeypatch):
    context = make_context(tmp_path)
    (context.generated_dir / ".git" / "info").mkdir(parents=True)
    write_pyproject(context, ["fakedep"])
    script = tmp_path / "ok.sh"
    script.write_text('#!/bin/sh\nmkdir -p "$MINT_PY_DEPS_TARGET"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MINT_PY_INSTALL_COMMAND", str(script))

    assert ADAPTER._ensure_dependencies_installed(context, ()) is None

    exclude = (context.generated_dir / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".mint-deps/" in exclude


def test_script_env_appends_deps_dir_to_required_src(tmp_path):
    context = make_context(tmp_path)
    deps_dir = context.generated_dir / ".mint-deps"
    deps_dir.mkdir()

    env = script_env(context, [tmp_path / "generated" / "other" / "src"])

    entries = env["MINT_REQUIRED_SRC"].split(os.pathsep)
    assert str(deps_dir) in entries
    assert entries[-1] == str(deps_dir)


def test_script_env_without_deps_dir_matches_previous_shape(tmp_path):
    context = make_context(tmp_path)
    env = script_env(context, [])
    assert "MINT_REQUIRED_SRC" not in env
