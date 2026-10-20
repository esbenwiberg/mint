"""Project scaffolding: `mint init` and `mint new` templates and writers."""

from __future__ import annotations

import os
from pathlib import Path
import re

from ..config import (
    DEFAULT_CONFORMANCE_DIR,
    DEFAULT_GENERATED_DIR,
    DEFAULT_SPECS_DIR,
    load_config,
)
from ..errors import MintError
from ..renderer import (
    is_model_provider,
    valid_renderer_providers,
)
from ..stacks import adapter_for_stack, known_stacks
from .context import configured_specs_dir

INIT_SKELETON = """mint Phase 0 skeleton
- config: mint.yaml
- specs: .mint/specs/example.mint.md
- resources: resources/
- generated output: .mint/generated/
- conformance tests: conformance/
- scripts: test_scripts/
"""

DEFAULT_MINT_YAML = """version: 1
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
  model: deterministic-python-cli-v0
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

PREPARE_ENVIRONMENT_SH = """#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

"$PYTHON_BIN" -m pip --version >/dev/null

if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
  exit 1
fi

echo "Python and pytest are available."
"""

RUN_UNIT_TESTS_SH = """#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
MODULE="${1:-example}"
GENERATED_DIR="${MINT_GENERATED_DIR:-.mint/generated/$MODULE}"

if [ ! -d "$GENERATED_DIR" ]; then
  echo "Generated module directory not found: $GENERATED_DIR" >&2
  exit 1
fi

cd "$GENERATED_DIR"
export PYTHONPATH="$PWD/src:${MINT_REQUIRED_SRC:-}:${PYTHONPATH:-}"

if [ "${MINT_SKIP_PYTEST_VERSION_CHECK:-0}" != "1" ]; then
  if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
    echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

"$PYTHON_BIN" -m pytest
"""

RUN_CONFORMANCE_TESTS_SH = """#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
MODULE="${1:-example}"
GENERATED_DIR="${MINT_GENERATED_DIR:-.mint/generated/$MODULE}"
CONFORMANCE_DIR="${MINT_CONFORMANCE_DIR:-conformance/$MODULE}"

if [ ! -d "$GENERATED_DIR" ]; then
  echo "Generated module directory not found: $GENERATED_DIR" >&2
  exit 1
fi

if [ ! -d "$CONFORMANCE_DIR" ]; then
  echo "Conformance test directory not found: $CONFORMANCE_DIR" >&2
  exit 1
fi

case "$GENERATED_DIR" in
  /*) GENERATED_SRC="$GENERATED_DIR/src" ;;
  *) GENERATED_SRC="$PWD/$GENERATED_DIR/src" ;;
esac

export PYTHONPATH="$GENERATED_SRC:${MINT_REQUIRED_SRC:-}:${PYTHONPATH:-}"

if [ "${MINT_SKIP_PYTEST_VERSION_CHECK:-0}" != "1" ]; then
  if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
    echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

"$PYTHON_BIN" -m pytest "$CONFORMANCE_DIR"
"""

EXAMPLE_MINT_MD = """---
module: example
description: Example task-list CLI and library
imports: []
requires: []
stack: python-cli
---

## definitions

- Task: item with text and a completed flag.

## implementation

- Use Python 3.12.
- Expose a small library API under `src/example/`.
- Provide a console command named `example-todo`.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call the public library API or CLI, not private helpers.

## functional

- id: FR1
  title: Add creates a task
  spec:
    - Calling `example-todo add "Buy milk"` stores a Task with that text.
    - Newly added tasks are incomplete.
  acceptance:
    - The add command exits 0, writes `[ ] Buy milk`, and a later list command prints `[ ] Buy milk`.

- id: FR2
  title: List shows tasks in insertion order
  spec:
    - Calling `example-todo list` prints all stored tasks.
    - Tasks appear in the order they were added.
  acceptance:
    - After adding "Buy milk" and then "Write notes", list output prints "Buy milk" before "Write notes".
    - With no stored tasks, list exits 0 and prints no task rows.
"""


def init_project(*, write: bool = False, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    if not write:
        return 0, INIT_SKELETON + "- Use `mint init --write` to create missing files.\n"

    lines = ["INIT mint project"]
    failures: list[str] = []
    specs_dir = DEFAULT_SPECS_DIR
    generated_dir = DEFAULT_GENERATED_DIR
    conformance_dir = DEFAULT_CONFORMANCE_DIR
    config_path = root / "mint.yaml"
    if config_path.exists():
        try:
            config = load_config(config_path)
            specs_dir = config.specs_dir
            generated_dir = config.generated_dir
            conformance_dir = config.conformance_dir
        except MintError:
            specs_dir = DEFAULT_SPECS_DIR
            generated_dir = DEFAULT_GENERATED_DIR
            conformance_dir = DEFAULT_CONFORMANCE_DIR

    for rel in [specs_dir, "resources", generated_dir, conformance_dir, "test_scripts"]:
        path = root / rel
        if path.exists():
            if path.is_dir():
                lines.append(f"- Kept existing {rel}/")
            else:
                failures.append(f"{rel}/ is blocked by a non-directory path")
        else:
            path.mkdir(parents=True)
            lines.append(f"- Created {rel}/")

    if failures:
        output = ["FAIL init", *lines]
        output.extend(f"- FAIL: {failure}" for failure in failures)
        return 1, "\n".join(output) + "\n"

    for rel in ["resources/.gitkeep", f"{generated_dir}/.gitkeep", f"{conformance_dir}/.gitkeep"]:
        _write_init_file(root, rel, "", executable=False, lines=lines, failures=failures)

    _write_init_file(root, "mint.yaml", DEFAULT_MINT_YAML, executable=False, lines=lines, failures=failures)
    _write_init_file(
        root,
        f"{specs_dir}/example.mint.md",
        EXAMPLE_MINT_MD,
        executable=False,
        lines=lines,
        failures=failures,
    )
    for rel, contents in [
        ("test_scripts/prepare_environment.sh", PREPARE_ENVIRONMENT_SH),
        ("test_scripts/run_unit_tests.sh", RUN_UNIT_TESTS_SH),
        ("test_scripts/run_conformance_tests.sh", RUN_CONFORMANCE_TESTS_SH),
    ]:
        _write_init_file(root, rel, contents, executable=True, lines=lines, failures=failures)

    _ensure_gitignore_entries(root, generated_dir, conformance_dir, lines=lines, failures=failures)

    if failures:
        output = ["FAIL init", *lines]
        output.extend(f"- FAIL: {failure}" for failure in failures)
        return 1, "\n".join(output) + "\n"

    lines.extend(
        [
            "- First smoke test: mint render example",
            "- New module: choose a module slug and model id, then run `mint new MODULE --renderer model --model MODEL_ID --prompt-version MODULE-v1`",
            "- Guided next step: mint next",
        ]
    )
    return 0, "\n".join(lines) + "\n"


def _write_init_file(
    root: Path,
    rel: str,
    contents: str,
    *,
    executable: bool,
    lines: list[str],
    failures: list[str],
) -> None:
    path = root / rel
    if path.exists():
        if path.is_file():
            lines.append(f"- Kept existing {rel}")
            if executable and not os.access(path, os.X_OK):
                lines.append(f"- WARN: {rel} is not executable; run chmod +x {rel}")
        else:
            failures.append(f"{rel} is blocked by a non-file path")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    lines.append(f"- Wrote {rel}")


def _ensure_gitignore_entries(
    root: Path,
    generated_dir: str,
    conformance_dir: str,
    *,
    lines: list[str],
    failures: list[str],
) -> None:
    path = root / ".gitignore"
    entries = _mint_gitignore_entries(generated_dir, conformance_dir)
    if path.exists() and not path.is_file():
        failures.append(".gitignore is blocked by a non-file path")
        return
    if not path.exists():
        path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        lines.append("- Wrote .gitignore")
        return

    text = path.read_text(encoding="utf-8")
    existing = set(text.splitlines())
    missing = [entry for entry in entries if entry not in existing]
    if not missing:
        lines.append("- Kept existing .gitignore")
        return
    prefix = "" if not text or text.endswith("\n") else "\n"
    separator = "" if not text.strip() else "\n"
    path.write_text(text + prefix + separator + "\n".join(missing) + "\n", encoding="utf-8")
    lines.append("- Updated .gitignore for Mint generated output")


def _mint_gitignore_entries(generated_dir: str, conformance_dir: str) -> list[str]:
    generated = generated_dir.rstrip("/")
    conformance = conformance_dir.rstrip("/")
    return [
        "# Mint generated artifacts",
        f"{generated}/*",
        f"!{generated}/.gitkeep",
        f"{conformance}/*",
        f"!{conformance}/.gitkeep",
    ]


def _has_value(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _is_placeholder_model(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower().replace("_", "-")
    return normalized in {
        "<model-id>",
        "model-id",
        "modelid",
        "your-model-id",
        "your-anthropic-model-id",
        "your-claude-model-id",
        "your-codex-model-id",
    }


def new_module(
    module: str,
    *,
    requires: list[str] | None = None,
    stack: str | None = None,
    renderer: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    root: Path | None = None,
) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    if not re.match(r"^[a-z][a-z0-9_-]*$", module):
        return (
            1,
            "FAIL new\n"
            f"- Invalid module name: {module}\n"
            "- Use a lowercase slug like calc-cli or taskstore.\n",
        )
    selected_stack = stack or "python-lib"
    try:
        adapter_for_stack(selected_stack)
    except MintError:
        return (
            1,
            "FAIL new\n"
            f"- Unsupported stack: {selected_stack}\n"
            f"- Use one of: {', '.join(known_stacks())}.\n",
        )
    selected_renderer = renderer.strip().lower() if renderer is not None else None
    if selected_renderer is not None and selected_renderer not in valid_renderer_providers():
        return (
            1,
            "FAIL new\n"
            f"- Invalid renderer: {renderer}\n"
            f"- Use one of: {', '.join(valid_renderer_providers())}.\n",
        )
    if selected_renderer is not None:
        renderer = selected_renderer
    if renderer is not None and is_model_provider(renderer) and (
        not _has_value(model) or not _has_value(prompt_version)
    ):
        return (
            1,
            "FAIL new\n"
            f"- --renderer {renderer} requires both --model and --prompt-version.\n"
            f"- Choose a real model id, then run: mint new {module} "
            f"--renderer {renderer} --model MODEL_ID --prompt-version {module}-v1\n",
        )
    if renderer is not None and is_model_provider(renderer) and _is_placeholder_model(model):
        return (
            1,
            "FAIL new\n"
            f"- --model must be a real model id, not {model!r}.\n"
            "- Replace MODEL_ID with the model you want to use before running the command.\n",
        )
    if (renderer is None or not is_model_provider(renderer)) and (model or prompt_version):
        return (
            1,
            "FAIL new\n"
            "- --model and --prompt-version require a model renderer "
            "(model, anthropic, claude-cli, or codex-cli).\n",
        )

    deps = requires or []
    invalid_deps = [dep for dep in deps if not re.match(r"^[a-z][a-z0-9_-]*$", dep)]
    if invalid_deps:
        return (
            1,
            "FAIL new\n"
            f"- Invalid --requires value(s): {', '.join(invalid_deps)}\n"
            "- Each required module must be a lowercase slug like calc-cli or taskstore.\n",
        )
    spec_path = root / configured_specs_dir(root) / f"{module}.mint.md"
    if spec_path.exists():
        return (
            1,
            f"FAIL new\n- Spec already exists: {spec_path.relative_to(root).as_posix()}\n",
        )

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        _starter_spec(
            module,
            deps,
            stack=selected_stack,
            renderer=renderer,
            model=model,
            prompt_version=prompt_version,
        ),
        encoding="utf-8",
    )
    hints = _new_module_hints(module, renderer)
    return (
        0,
        "NEW "
        + module
        + "\n"
        + f"- Wrote {spec_path.relative_to(root).as_posix()}\n"
        + "".join(f"- {hint}\n" for hint in hints),
    )


def _starter_spec(
    module: str,
    requires: list[str],
    *,
    stack: str = "python-lib",
    renderer: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> str:
    deps = _yaml_list(requires)
    renderer_lines: list[str] = []
    if renderer is not None and is_model_provider(renderer):
        renderer_lines.append(f"rendererProvider: {renderer}")
        if model:
            renderer_lines.append(f"rendererModel: {model}")
        if prompt_version:
            renderer_lines.append(f"rendererPromptVersion: {prompt_version}")
    elif renderer in {"local", "deterministic"}:
        renderer_lines.append(f"rendererProvider: {renderer}")
    renderer_block = ("\n" + "\n".join(renderer_lines)) if renderer_lines else ""
    package = module.replace("-", "_")
    if stack.startswith("typescript-"):
        implementation = """- Use TypeScript with Node and npm-compatible package scripts.
- Expose the public API from `src/index.ts`.
- Keep generated implementation under `src/` and unit tests under `tests/`.
- `package.json` scripts must include `typecheck`, `test:unit`, and `test:conformance`.
- Use `tsc --noEmit` for type checking and Vitest for tests."""
        test = """- Unit tests use Vitest.
- Conformance tests use Vitest and call only the public API.
- Include at least one edge or error case before rendering real code."""
    else:
        implementation = f"""- Use Python 3.12.
- Expose the public API under `src/{package}/`.
- Unit tests use pytest."""
        test = """- Conformance tests use pytest.
- Conformance tests call only the public API.
- Include at least one edge or error case before rendering real code."""
    return f"""---
module: {module}
description: {module} module
imports: {deps}
requires: {deps}
stack: {stack}{renderer_block}
---

## definitions

- {module}: the public module being generated.

## implementation

{implementation}

## test

{test}

## functional

- id: FR1
  title: Implement the first public behavior
  spec:
    - Replace this starter behavior with one concrete public capability.
  acceptance:
    - After calling the public API with a representative input, it returns the expected output.
"""


def _new_module_hints(module: str, renderer: str | None) -> list[str]:
    hints = [f"Next: mint lint {module}"]
    if renderer is not None and is_model_provider(renderer):
        hints.extend(
            [
                f"Offline render will replay cassettes if they exist: mint render {module}",
                f"First live render/record: MINT_LIVE=1 mint live-smoke {module}",
            ]
        )
    elif renderer in {"local", "deterministic"}:
        hints.append(
            "Add a matching deterministic template before rendering, or switch the spec "
            "to rendererProvider: model (or another model provider)."
        )
    else:
        hints.append(
            "Before rendering, add a deterministic template or set rendererProvider: model "
            "(or another model provider) for live/replay rendering."
        )
    return hints


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(items) + "]"
