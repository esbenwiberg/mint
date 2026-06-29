from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from .config import (
    DEFAULT_CONFORMANCE_DIR,
    DEFAULT_GENERATED_DIR,
    DEFAULT_SPECS_DIR,
    MintConfig,
    load_config,
)
from .errors import MintError
from .gitutil import commit_all, ensure_git_repo, git_head, reset_hard
from .hashing import hash_generated_files, hash_json
from .modgraph import build_render_order
from .renderer import (
    RenderRequest,
    apply_patch,
    cassette_model,
    get_renderer,
    is_anthropic_live_provider,
    is_model_provider,
    valid_renderer_providers,
    validate_patch,
)
from .renderer.base import Renderer
from .renderer.model import ModelClient, ModelOutputError
from .renderer.templates import known_templates
from .specs import FunctionalUnit, Spec, parse_spec_file
from .state import (
    append_render_log,
    fresh_metadata,
    load_metadata,
    now_iso,
    record_by_unit,
    refresh_metadata_hashes,
    trim_records,
    unit_text_hash,
    write_attempt,
    write_metadata,
)
from .stacks import StackAdapter, adapter_for_stack, known_stacks
from .test_quality import evaluate_test_quality, format_test_quality_verdict

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


@dataclass(frozen=True)
class ModuleContext:
    root: Path
    module: str
    config: MintConfig
    spec: Spec
    generated_dir: Path
    conformance_dir: Path

    def spec_path(self, module: str) -> Path:
        return self.root / self.config.specs_dir / f"{module}.mint.md"

    def generated_dir_for(self, module: str) -> Path:
        return self.root / self.config.generated_dir / module

    def src_dir_for(self, module: str) -> Path:
        return self.generated_dir_for(module) / "src"

    @property
    def stack_adapter(self) -> StackAdapter:
        return adapter_for_stack(self.spec.stack)


@dataclass(frozen=True)
class RenderPlan:
    start_index: int
    end_index: int
    reason: str
    noop: bool = False


@dataclass(frozen=True)
class ContextHashes:
    imported_context_hash: str
    required_module_code_hash: str
    required_order: tuple[str, ...]


@dataclass
class BudgetTracker:
    max_attempts: int
    max_tokens_estimate: int
    attempts: int = 0
    tokens_estimate: int = 0

    def record(self, *, prompt: str | None, response: str | None) -> None:
        self.attempts += 1
        self.tokens_estimate += _estimate_tokens(prompt or "") + _estimate_tokens(response or "")

    def exceeded(self) -> str | None:
        if self.max_attempts > 0 and self.attempts > self.max_attempts:
            return f"attempt budget exceeded ({self.attempts}/{self.max_attempts})"
        if self.max_tokens_estimate > 0 and self.tokens_estimate > self.max_tokens_estimate:
            return (
                "token budget exceeded "
                f"({self.tokens_estimate}/{self.max_tokens_estimate} estimated tokens)"
            )
        return None


class PatchAttemptFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        prompt: str | None = None,
        response: str | None = None,
        patch: Any | None = None,
        renderer: str | None = None,
        cassette_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.prompt = prompt
        self.response = response
        self.patch = patch
        self.renderer = renderer
        self.cassette_id = cassette_id


def load_context(module: str, root: Path | None = None) -> ModuleContext:
    root = (root or Path.cwd()).resolve()
    config = load_config(root / "mint.yaml")
    spec = parse_spec_file(root / config.specs_dir / f"{module}.mint.md")
    if spec.module != module:
        raise MintError(
            f"Spec module '{spec.module}' does not match requested module '{module}'. "
            f"Fix: rename the spec file or the 'module' frontmatter key."
        )
    generated_dir = root / config.generated_dir / module
    conformance_dir = root / config.conformance_dir / module
    return ModuleContext(root, module, config, spec, generated_dir, conformance_dir)


def _spec_loader(root: Path, specs_dir: str):
    def load(module: str) -> Spec:
        spec = parse_spec_file(root / specs_dir / f"{module}.mint.md")
        if spec.module != module:
            raise MintError(
                f"Spec module '{spec.module}' does not match requested module '{module}'."
            )
        return spec

    return load


def resolve_required_order(context: ModuleContext) -> list[str]:
    """Transitive required modules in dependency order, excluding the module itself."""
    order = build_render_order(context.module, _spec_loader(context.root, context.config.specs_dir))
    return [module for module in order if module != context.module]


def resolve_imported_specs(context: ModuleContext) -> list[Spec]:
    loader = _spec_loader(context.root, context.config.specs_dir)
    return [loader(name) for name in context.spec.imports]


def compute_context_hashes(context: ModuleContext) -> ContextHashes:
    imported = sorted(
        (spec.imported_context_ir() for spec in resolve_imported_specs(context)),
        key=lambda item: item["module"],
    )
    imported_hash = hash_json(imported)

    required_order = resolve_required_order(context)
    required_entries = sorted(
        (
            {"module": module, "codeHash": hash_generated_files(context.generated_dir_for(module))}
            for module in required_order
        ),
        key=lambda item: item["module"],
    )
    required_hash = hash_json(required_entries)
    return ContextHashes(imported_hash, required_hash, tuple(required_order))


def renderer_provider(context: ModuleContext) -> str:
    return selected_renderer_provider(context.spec, context.config)


def renderer_model(context: ModuleContext) -> str:
    return context.spec.renderer_model or context.config.renderer.model


def renderer_prompt_version(context: ModuleContext) -> str:
    return context.spec.renderer_prompt_version or context.config.renderer.prompt_version


def parse_module(module: str) -> str:
    context = load_context(module)
    return json.dumps(context.spec.to_ir(), indent=2, sort_keys=True) + "\n"


def configured_specs_dir(root: Path) -> str:
    config_path = root / "mint.yaml"
    if not config_path.exists():
        return DEFAULT_SPECS_DIR
    return load_config(config_path).specs_dir


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


def lint_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    path = root / configured_specs_dir(root) / f"{module}.mint.md"
    failures: list[str] = []
    warnings: list[str] = []

    try:
        spec = parse_spec_file(path)
    except MintError as exc:
        return 1, f"FAIL lint {module}\n- {exc}\n"

    for unit in spec.functional_units:
        acceptance_text = " ".join(unit.acceptance)
        if _looks_vague(acceptance_text):
            failures.append(
                f"{unit.id} acceptance is vague; name the observable output, state, or error."
            )
        if not _has_testable_assertion(acceptance_text):
            failures.append(
                f"{unit.id} acceptance has no testable assertion; add a concrete return, output, "
                "exit code, stored state, or error condition."
            )

    combined = " ".join(
        spec.implementation
        + spec.test
        + [item for unit in spec.functional_units for item in unit.spec + unit.acceptance]
    ).lower()
    if not any(term in combined for term in _EDGE_HINT_TERMS):
        warnings.append(
            "No edge-case coverage hint found; consider empty, invalid, missing, duplicate, "
            "unknown, or error cases."
        )

    status = "FAIL" if failures else "PASS"
    lines = [
        f"{status} lint {module}",
        f"- Parsed {path.relative_to(root).as_posix()}",
        f"- Functional units: {len(spec.functional_units)}",
    ]
    lines.extend(f"- FAIL: {failure}" for failure in failures)
    lines.extend(f"- WARN: {warning}" for warning in warnings)
    if not failures and not warnings:
        lines.append("- Spec quality checks passed.")
    return (1 if failures else 0), "\n".join(lines) + "\n"


def next_module(module: str | None = None, *, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    if module is None:
        return next_project(root=root)

    spec_path = root / configured_specs_dir(root) / f"{module}.mint.md"
    lines = [f"NEXT {module}"]
    if not spec_path.exists():
        lines.extend(
            [
                "- State: no spec exists yet.",
                f"- Next command: choose a model id, then run `mint new {module} --renderer model --model MODEL_ID --prompt-version {module}-v1`",
                "- Then edit the spec, run `mint lint`, and record once with `MINT_LIVE=1 mint live-smoke`.",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    lint_status, lint_output = lint_module(module, root=root)
    if lint_status != 0:
        lines.extend(
            [
                "- State: spec needs lint fixes.",
                f"- Next command: mint lint {module}",
                "Details:",
                *_prefix_lines(lint_output, "  "),
            ]
        )
        return 0, "\n".join(lines) + "\n"

    try:
        context = load_context(module, root)
    except MintError as exc:
        lines.extend(
            [
                "- State: spec could not be loaded.",
                f"- Next command: mint lint {module}",
                f"- {exc}",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    health_status, health_output = healthcheck_module(module, root=root)
    if health_status != 0:
        command = f"mint healthcheck {module}"
        replay_issue = model_replay_issue(context.spec, context.config, context.root)
        if replay_issue:
            command = f"MINT_LIVE=1 mint live-smoke {module}"
        lines.extend(
            [
                "- State: pre-render checks need attention.",
                f"- Next command: {command}",
                "Details:",
                *_prefix_lines(health_output, "  "),
            ]
        )
        return 0, "\n".join(lines) + "\n"

    hashes = compute_context_hashes(context)
    metadata = load_metadata(context.generated_dir)
    plan = determine_render_plan(context, metadata, None, None, False, hashes)
    if plan.noop:
        lines.extend(
            [
                "- State: generated output is current.",
                f"- Next command: mint report {module}",
                "- To change behavior, edit the spec and run `mint render` again.",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    command = f"mint render {module}"
    if metadata is not None and plan.start_index > 0:
        command += f" --from {context.spec.functional_units[plan.start_index].id}"
    lines.extend(
        [
            f"- State: ready to render ({plan.reason}).",
            f"- Next command: {command}",
        ]
    )
    return 0, "\n".join(lines) + "\n"


def next_project(*, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    lines = ["NEXT"]
    if not (root / "mint.yaml").exists():
        lines.extend(
            [
                "- State: no Mint project found in this directory.",
                "- Next command: mint init --write",
                "- Then run: mint next",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    try:
        config = load_config(root / "mint.yaml")
    except MintError as exc:
        lines.extend(
            [
                "- State: config could not be loaded.",
                "- Next command: mint doctor",
                f"- {exc}",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    spec_dir = root / config.specs_dir
    spec_paths = sorted(spec_dir.glob("*.mint.md"))
    if not spec_paths:
        lines.extend(
            [
                "- State: project has no specs yet.",
                "- Next command: choose a module slug and model id, then run `mint new MODULE --renderer model --model MODEL_ID --prompt-version MODULE-v1`",
                "- Then run: mint next MODULE",
            ]
        )
        return 0, "\n".join(lines) + "\n"

    modules: list[str] = []
    parse_failures: list[str] = []
    for spec_path in spec_paths:
        try:
            modules.append(parse_spec_file(spec_path).module)
        except MintError as exc:
            parse_failures.append(f"{spec_path.relative_to(root).as_posix()}: {exc}")

    if parse_failures:
        lines.extend(
            [
                "- State: at least one spec cannot be parsed.",
                "- Next command: mint lint <module>",
                "Details:",
                *(f"  - {failure}" for failure in parse_failures),
            ]
        )
        return 0, "\n".join(lines) + "\n"

    if len(modules) == 1:
        return next_module(modules[0], root=root)

    lines.extend(
        [
            f"- State: project has {len(modules)} specs.",
            "- Next command: mint next <module>",
            "- Modules: " + ", ".join(sorted(modules)),
        ]
    )
    return 0, "\n".join(lines) + "\n"


def doctor_project(*, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    failures: list[str] = []
    warnings: list[str] = []
    messages: list[str] = []

    try:
        config = load_config(root / "mint.yaml")
    except MintError as exc:
        return (
            1,
            "FAIL doctor\n"
            f"- {exc}\n"
            "- Next command: mint init --write\n"
            "- Then run: mint next\n",
        )

    messages.append(f"Config: {config.path.relative_to(root).as_posix()}")
    for label, script in [
        ("prepare", config.scripts.prepare),
        ("unit", config.scripts.unit),
        ("conformance", config.scripts.conformance),
    ]:
        script_path = root / script
        if not script_path.exists():
            failures.append(f"{label} script missing: {script} (fix: {_missing_file_fix()})")
        elif not os.access(script_path, os.X_OK):
            failures.append(f"{label} script is not executable: {script} (fix: chmod +x {script})")
        else:
            messages.append(f"{label} script: {script}")

    probe_env = os.environ.copy()
    probe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    pytest_check = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        cwd=root,
        env=probe_env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if pytest_check.returncode == 0:
        messages.append(f"pytest: {pytest_check.stdout.strip()}")
    else:
        failures.append("pytest is not runnable (fix: pip install -e '.[dev]')")

    spec_dir = root / config.specs_dir
    spec_paths = sorted(spec_dir.glob("*.mint.md"))
    if not spec_paths:
        failures.append(
            f"No specs found under {config.specs_dir}/ "
            "(fix: run `mint new MODULE --renderer model --model MODEL_ID --prompt-version MODULE-v1`, "
            "then `mint next MODULE`)"
        )
    model_specs: list[str] = []
    for spec_path in spec_paths:
        try:
            spec = parse_spec_file(spec_path)
        except MintError as exc:
            failures.append(str(exc))
            continue
        messages.append(f"Spec: {spec_path.relative_to(root).as_posix()}")
        try:
            adapter = adapter_for_stack(spec.stack)
        except MintError as exc:
            failures.append(str(exc))
            continue
        messages.append(f"Stack: {spec.module} -> {adapter.name} ({spec.stack})")
        if adapter.name != "python":
            stack_health = adapter.healthcheck(load_context(spec.module, root))
            messages.extend(
                message for message in stack_health.messages if not message.startswith("Stack adapter:")
            )
            failures.extend(stack_health.failures)
        template_issue = local_template_issue(spec, config)
        if template_issue:
            warnings.append(template_issue)
        provider = selected_renderer_provider(spec, config)
        if is_model_provider(provider):
            model_specs.append(spec.module)
            replay_issue = model_replay_issue(spec, config, root)
            if replay_issue:
                failures.append(replay_issue)
        for dep in sorted(set(spec.imports + spec.requires)):
            dep_path = root / config.specs_dir / f"{dep}.mint.md"
            if not dep_path.exists():
                failures.append(
                    f"{spec.module} references missing spec {dep_path.relative_to(root).as_posix()}"
                )

    if model_specs and os.environ.get("MINT_LIVE") != "1":
        cassette_root = root / "resources" / "cassettes"
        cassettes = list((cassette_root / "v1").glob("*.json"))
        if cassettes:
            messages.append(
                f"Replay cassettes: {len(cassettes)} in "
                f"{cassette_root.relative_to(root).as_posix()}"
            )
    elif model_specs:
        warnings.append("MINT_LIVE=1 is set; doctor will allow live recording.")
    else:
        messages.append(f"Renderer: {config.renderer.provider} ({config.renderer.model})")

    status = "FAIL" if failures else "PASS"
    lines = [f"{status} doctor"]
    lines.extend(f"- {message}" for message in messages)
    lines.extend(f"- FAIL: {failure}" for failure in failures)
    lines.extend(f"- WARN: {warning}" for warning in warnings)
    return (1 if failures else 0), "\n".join(lines) + "\n"


def healthcheck_module(
    module: str,
    *,
    root: Path | None = None,
    allow_missing_replay: bool = False,
    allow_missing_generated_metadata: bool = False,
) -> tuple[int, str]:
    messages: list[str] = []
    failures: list[str] = []

    try:
        context = load_context(module, root)
        messages.append(f"Spec parsed: {len(context.spec.functional_units)} functional units")
    except MintError as exc:
        return 1, f"FAIL {module}\n- {exc}\n"

    messages.append(f"Config parsed: {context.config.path.relative_to(context.root).as_posix()}")
    try:
        adapter = context.stack_adapter
    except MintError as exc:
        adapter = None
        failures.append(str(exc))
    else:
        stack_health = adapter.healthcheck(context)
        messages.extend(stack_health.messages)
        failures.extend(stack_health.failures)

    template_issue = local_template_issue(context.spec, context.config)
    if template_issue:
        failures.append(template_issue)
    replay_issue = model_replay_issue(context.spec, context.config, context.root)
    if replay_issue and not allow_missing_replay:
        failures.append(replay_issue)
    replay_count = matching_replay_cassette_count(context.spec, context.config, context.root)
    if replay_count:
        messages.append(
            f"Replay cassette candidates: {replay_count} for {context.module} "
            f"in {(context.root / 'resources' / 'cassettes').relative_to(context.root).as_posix()}"
        )

    # Imports and requires must resolve to real spec files.
    for name in context.spec.imports:
        if not context.spec_path(name).exists():
            failures.append(
                f"Imported module spec missing: {context.spec_path(name).relative_to(context.root).as_posix()}"
            )
    try:
        required_order = resolve_required_order(context)
        if required_order:
            messages.append(f"Requires (build order): {', '.join(required_order)}")
    except MintError as exc:
        failures.append(str(exc))

    for unit in context.spec.functional_units:
        for resource in unit.resources:
            resource_path = context.root / resource
            if not resource_path.exists():
                failures.append(f"Linked resource missing for {unit.id}: {resource}")

    if context.generated_dir.exists():
        try:
            metadata = load_metadata(context.generated_dir)
        except json.JSONDecodeError as exc:
            failures.append(f"Generated metadata is invalid JSON: {exc}")
        else:
            if metadata is None:
                message = (
                    f"Generated repo is missing metadata: {context.generated_dir}. "
                    f"Fix: run `mint clean {context.module} --yes` before retrying. "
                    f"Next: mint render {context.module} --force if you intentionally "
                    "want to replace this directory."
                )
                if allow_missing_generated_metadata:
                    messages.append(message)
                else:
                    failures.append(message)
            else:
                messages.append(
                    f"Generated repo: {context.generated_dir.relative_to(context.root).as_posix()}"
                )
                messages.append(f"Last successful unit: {metadata.get('lastSuccessfulUnitId') or 'none'}")
    else:
        messages.append("Generated repo: absent")

    status = "FAIL" if failures else "PASS"
    lines = [f"{status} {module}"]
    lines.extend(f"- {message}" for message in messages)
    lines.extend(f"- {failure}" for failure in failures)
    return (1 if failures else 0), "\n".join(lines) + "\n"


def selected_renderer_provider(spec: Spec, config: MintConfig) -> str:
    return (spec.renderer_provider or config.renderer.provider).strip().lower()


def _missing_file_fix() -> str:
    return "create the file, or run `mint init --write` to restore default project files"


def local_template_issue(spec: Spec, config: MintConfig) -> str | None:
    provider = selected_renderer_provider(spec, config)
    if provider not in {"local", "deterministic"}:
        return None
    template = spec.template or spec.module
    templates = known_templates()
    if template in templates:
        return None
    return (
        f"{spec.module} uses local renderer but no deterministic template '{template}' exists. "
        f"Known templates: {', '.join(templates)}. "
        "Fix: add a matching template, set `template:` to a known template, or set "
        "`rendererProvider: model` (or another model provider) and record with "
        "`MINT_LIVE=1 mint live-smoke "
        f"{spec.module}`."
    )


def model_replay_issue(spec: Spec, config: MintConfig, root: Path) -> str | None:
    provider = selected_renderer_provider(spec, config)
    if not is_model_provider(provider) or os.environ.get("MINT_LIVE") == "1":
        return None
    if matching_replay_cassette_count(spec, config, root) > 0:
        return None
    model = spec.renderer_model or config.renderer.model
    prompt_version = spec.renderer_prompt_version or config.renderer.prompt_version
    return (
        f"Replay cassettes missing for model renderer spec {spec.module} "
        f"(model {model}, prompt {prompt_version}). "
        f"Fix: record with MINT_LIVE=1 mint live-smoke {spec.module}."
    )


def matching_replay_cassette_count(spec: Spec, config: MintConfig, root: Path) -> int:
    provider = selected_renderer_provider(spec, config)
    if not is_model_provider(provider):
        return 0
    cassette_dir = root / "resources" / "cassettes" / "v1"
    if not cassette_dir.exists():
        return 0
    model = cassette_model(provider, spec.renderer_model or config.renderer.model)
    prompt_version = spec.renderer_prompt_version or config.renderer.prompt_version
    count = 0
    for path in sorted(cassette_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        request = data.get("request") if isinstance(data, dict) else None
        if not isinstance(request, dict):
            continue
        if (
            request.get("module") == spec.module
            and data.get("model") == model
            and data.get("promptVersion") == prompt_version
        ):
            count += 1
    return count


def status_module(module: str) -> str:
    context = load_context(module)
    metadata = load_metadata(context.generated_dir)
    hashes = compute_context_hashes(context)
    plan = determine_render_plan(context, metadata, None, None, False, hashes)

    lines = [
        f"Module: {module}",
        f"Spec: {context.spec.path.relative_to(context.root).as_posix()}",
        f"Generated: {context.generated_dir.relative_to(context.root).as_posix()}",
        f"Renderer: {renderer_provider(context)} ({renderer_model(context)})",
    ]
    if hashes.required_order:
        lines.append(f"Requires: {', '.join(hashes.required_order)}")
    if metadata is None:
        lines.extend(
            [
                "Last successful unit: none",
                "Spec hash: new",
                "Non-functional hash: new",
                "Imported context: new",
                "Required module code: new",
                f"Suggested render: mint render {module}",
            ]
        )
    else:
        lines.extend(
            [
                f"Last successful unit: {metadata.get('lastSuccessfulUnitId') or 'none'}",
                f"Spec hash: {_changed(metadata.get('specHash'), context.spec.spec_hash)}",
                f"Non-functional hash: {_changed(metadata.get('nonFunctionalSpecHash'), context.spec.non_functional_hash)}",
                f"Imported context: {_changed(metadata.get('importedContextHash'), hashes.imported_context_hash)}",
                f"Required module code: {_changed(metadata.get('requiredModuleCodeHash'), hashes.required_module_code_hash)}",
            ]
        )
        if plan.noop:
            lines.append("Suggested render: no-op")
        else:
            start_unit = context.spec.functional_units[plan.start_index].id
            lines.append(f"Suggested render ({plan.reason}): mint render {module} --from {start_unit}")
    return "\n".join(lines) + "\n"


def report_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    context = load_context(module, root)
    metadata = load_metadata(context.generated_dir)
    if metadata is None:
        return (
            1,
            f"FAIL report {module}\n"
            f"- No generated metadata found at {metadata_path_for_message(context)}\n"
            f"- Next: mint render {module}\n",
        )

    report = build_run_report(context, metadata)
    report_path = write_run_report(context, report)
    return 0, format_run_report(context, report, report_path)


def live_smoke_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    context = load_context(module, root)
    provider = renderer_provider(context)
    if not is_model_provider(provider):
        return (
            1,
            f"FAIL live-smoke {module}\n"
            f"- {context.spec.path.relative_to(context.root).as_posix()} uses renderer "
            f"{provider!r}; live smoke requires a model renderer "
            "(model, anthropic, claude-cli, or codex-cli).\n",
        )
    if os.environ.get("MINT_LIVE") != "1":
        return (
            1,
            f"FAIL live-smoke {module}\n"
            "- MINT_LIVE=1 is required so live provider calls are always explicit.\n"
            f"- Next: MINT_LIVE=1 mint live-smoke {module}\n",
        )
    if is_anthropic_live_provider(provider) and not os.environ.get("ANTHROPIC_API_KEY"):
        return (
            1,
            f"FAIL live-smoke {module}\n"
            "- ANTHROPIC_API_KEY is not set.\n"
            "- Next: export ANTHROPIC_API_KEY=... and rerun "
            f"`MINT_LIVE=1 mint live-smoke {module}`.\n",
        )
    return render_module(module, force=True, root=context.root)


def build_run_report(context: ModuleContext, metadata: dict[str, Any]) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    totals = {
        "attempts": 0,
        "promptTokensEstimate": 0,
        "responseTokensEstimate": 0,
        "totalTokensEstimate": 0,
        "costEstimateUsd": 0.0,
    }

    for record in metadata.get("functionalUnits", []):
        if not isinstance(record, dict):
            continue
        unit_id = str(record.get("id", ""))
        attempts = _attempt_records(context, unit_id)
        prompt_tokens = sum(item["tokens"]["prompt"] for item in attempts)
        response_tokens = sum(item["tokens"]["response"] for item in attempts)
        total_tokens = prompt_tokens + response_tokens
        unit_report = {
            "id": unit_id,
            "title": record.get("title"),
            "status": record.get("status"),
            "attempts": record.get("attempts", {}),
            "attemptManifests": attempts,
            "cassetteIds": sorted(
                {
                    item["cassetteId"]
                    for item in attempts
                    if isinstance(item.get("cassetteId"), str) and item["cassetteId"]
                }
            ),
            "classification": _classifications(attempts),
            "testQuality": record.get("testQuality"),
            "wallClockSeconds": _wall_clock_seconds(record.get("startedAt"), record.get("finishedAt")),
            "tokens": {
                "prompt": prompt_tokens,
                "response": response_tokens,
                "total": total_tokens,
                "estimate": True,
            },
            "costEstimateUsd": 0.0,
        }
        units.append(unit_report)
        totals["attempts"] += len(attempts)
        totals["promptTokensEstimate"] += prompt_tokens
        totals["responseTokensEstimate"] += response_tokens
        totals["totalTokensEstimate"] += total_tokens

    return {
        "version": 1,
        "module": context.module,
        "renderId": metadata.get("renderId"),
        "generatedDir": context.generated_dir.relative_to(context.root).as_posix(),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "promptVersion": metadata.get("promptVersion"),
        "lastSuccessfulUnitId": metadata.get("lastSuccessfulUnitId"),
        "generatedCodeHash": metadata.get("generatedCodeHash"),
        "units": units,
        "totals": totals,
        "pricing": {
            "configured": False,
            "note": "Token counts are character-based estimates; no provider price table is configured.",
        },
    }


def write_run_report(context: ModuleContext, report: dict[str, Any]) -> Path:
    reports_dir = context.generated_dir / ".mintgen" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    render_id = str(report.get("renderId") or "latest")
    report_path = reports_dir / f"{render_id}.json"
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report_path.write_text(text, encoding="utf-8")
    latest = reports_dir / "latest.json"
    latest.write_text(text, encoding="utf-8")
    (reports_dir / "latest.txt").write_text(
        format_run_report(context, report, latest),
        encoding="utf-8",
    )
    return latest


def format_run_report(context: ModuleContext, report: dict[str, Any], report_path: Path) -> str:
    totals = report.get("totals", {})
    lines = [
        f"RUN REPORT {context.module}",
        f"- Render id: {report.get('renderId')}",
        f"- Renderer: {report.get('provider')} ({report.get('model')})",
        f"- Prompt version: {report.get('promptVersion')}",
        f"- Last successful unit: {report.get('lastSuccessfulUnitId')}",
        f"- Attempts: {totals.get('attempts', 0)}",
        f"- Tokens estimate: {totals.get('totalTokensEstimate', 0)} "
        f"(prompt {totals.get('promptTokensEstimate', 0)}, "
        f"response {totals.get('responseTokensEstimate', 0)})",
        f"- Cost estimate: ${float(totals.get('costEstimateUsd', 0.0)):.6f}",
        f"- Report JSON: {report_path.relative_to(context.root).as_posix()}",
        "Units:",
    ]
    for unit in report.get("units", []):
        cassette_count = len(unit.get("cassetteIds", []))
        lines.append(
            f"- {unit.get('id')}: {unit.get('status')} | "
            f"{len(unit.get('attemptManifests', []))} attempts | "
            f"{unit.get('tokens', {}).get('total', 0)} tokens | "
            f"{cassette_count} cassette ids"
        )
    return "\n".join(lines) + "\n"


_VAGUE_TERMS = {
    "works",
    "properly",
    "correctly",
    "as needed",
    "etc",
    "todo",
    "tbd",
    "nice",
}

_TESTABLE_TERMS = {
    "return",
    "returns",
    "show",
    "shows",
    "print",
    "prints",
    "raise",
    "raises",
    "exit",
    "exits",
    "store",
    "stores",
    "write",
    "writes",
    "reject",
    "rejects",
    "accept",
    "accepts",
    "fail",
    "fails",
    "pass",
    "passes",
    "contain",
    "contains",
    "surface",
    "surfaces",
    "emit",
    "emits",
    "equal",
    "equals",
    "before",
    "after",
}

_EDGE_HINT_TERMS = {
    "empty",
    "invalid",
    "missing",
    "unknown",
    "duplicate",
    "zero",
    "edge",
    "error",
    "fail",
    "reject",
    "no ",
}


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
        implementation = f"""- Use TypeScript with Node and npm-compatible package scripts.
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


def _looks_vague(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _VAGUE_TERMS)


def _has_testable_assertion(text: str) -> bool:
    lower = text.lower()
    if any(symbol in text for symbol in ["==", "!=", "<=", ">=", " exit ", "$?"]):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _TESTABLE_TERMS)


def _prefix_lines(text: str, prefix: str) -> list[str]:
    return [prefix + line for line in text.rstrip().splitlines()]


def metadata_path_for_message(context: ModuleContext) -> str:
    return (context.generated_dir / ".mintgen" / "module.json").relative_to(context.root).as_posix()


def _attempt_records(context: ModuleContext, unit_id: str) -> list[dict[str, Any]]:
    attempts_dir = context.generated_dir / ".mintgen" / "attempts" / unit_id
    if not attempts_dir.exists():
        return []

    attempts: list[dict[str, Any]] = []
    for path in sorted(attempts_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        prompt_text = _read_attempt_artifact(context, data.get("promptPath"))
        response_text = _read_attempt_artifact(context, data.get("responsePath"))
        attempts.append(
            {
                "path": path.relative_to(context.root).as_posix(),
                "phase": data.get("phase"),
                "attempt": data.get("attempt"),
                "classification": data.get("classification"),
                "exitCode": data.get("exitCode"),
                "renderer": data.get("renderer"),
                "cassetteId": data.get("cassetteId"),
                "tokens": {
                    "prompt": _estimate_tokens(prompt_text),
                    "response": _estimate_tokens(response_text),
                    "total": _estimate_tokens(prompt_text) + _estimate_tokens(response_text),
                    "estimate": True,
                },
            }
        )
    return attempts


def _read_attempt_artifact(context: ModuleContext, relative: Any) -> str:
    if not isinstance(relative, str) or not relative:
        return ""
    path = context.generated_dir / relative
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _classifications(attempts: list[dict[str, Any]]) -> list[str]:
    return [
        str(item["classification"])
        for item in attempts
        if isinstance(item.get("classification"), str) and item["classification"]
    ]


def _wall_clock_seconds(started_at: Any, finished_at: Any) -> float | None:
    if not isinstance(started_at, str) or not isinstance(finished_at, str):
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (end - start).total_seconds())


def _changed(stored: Any, current: Any) -> str:
    return "changed" if stored != current else "unchanged"


def render_module(
    module: str,
    from_unit: str | None = None,
    unit_range: str | None = None,
    force: bool = False,
    *,
    model_client: ModelClient | None = None,
    root: Path | None = None,
) -> tuple[int, str]:
    """Render ``module`` and every module it requires, in dependency order.

    Required modules render with their default (incremental) plan; the named module
    additionally honours --from / --range / --force.
    """
    top = load_context(module, root)
    order = build_render_order(module, _spec_loader(top.root, top.config.specs_dir))
    budget = BudgetTracker(
        max_attempts=top.config.limits.max_render_attempts,
        max_tokens_estimate=top.config.limits.max_render_tokens_estimate,
    )

    sections: list[str] = []
    for current in order:
        is_top = current == module
        status, output = render_single_module(
            current,
            from_unit=from_unit if is_top else None,
            unit_range=unit_range if is_top else None,
            force=force if is_top else False,
            model_client=model_client,
            root=top.root,
            budget=budget,
        )
        sections.append(output)
        if status != 0:
            return status, "".join(sections)
    return 0, "".join(sections)


def render_single_module(
    module: str,
    from_unit: str | None,
    unit_range: str | None,
    force: bool,
    *,
    model_client: ModelClient | None,
    root: Path | None = None,
    budget: BudgetTracker | None = None,
) -> tuple[int, str]:
    context = load_context(module, root)
    adapter = context.stack_adapter
    if budget is None:
        budget = BudgetTracker(
            max_attempts=context.config.limits.max_render_attempts,
            max_tokens_estimate=context.config.limits.max_render_tokens_estimate,
        )
    health_status, health_output = healthcheck_module(
        module,
        root=context.root,
        allow_missing_replay=model_client is not None,
        allow_missing_generated_metadata=force,
    )
    if health_status != 0:
        return health_status, health_output

    prepare = adapter.prepare(context)
    if prepare.returncode != 0:
        return 1, format_script_failure("prepare", prepare)

    hashes = compute_context_hashes(context)
    metadata = load_metadata(context.generated_dir)
    plan = determine_render_plan(context, metadata, from_unit, unit_range, force, hashes)
    if plan.noop:
        if metadata is not None:
            write_run_report(context, build_run_report(context, metadata))
        return 0, f"NOOP {module}\n- Generated output already matches spec and inputs.\n"

    prepare_render_workspace(context, metadata, plan, hashes)
    ensure_git_repo(context.generated_dir, module)
    metadata = load_metadata(context.generated_dir)
    if plan.start_index == 0 or metadata is None:
        metadata = fresh_metadata(
            context.spec,
            context.config,
            context.generated_dir,
            imported_context_hash=hashes.imported_context_hash,
            required_module_code_hash=hashes.required_module_code_hash,
        )
    else:
        refresh_metadata_hashes(
            metadata,
            context.spec,
            context.generated_dir,
            imported_context_hash=hashes.imported_context_hash,
            required_module_code_hash=hashes.required_module_code_hash,
        )

    renderer = get_renderer(
        renderer_provider(context),
        model=renderer_model(context),
        prompt_version=renderer_prompt_version(context),
        model_client=model_client,
        cassette_dir=context.root / "resources" / "cassettes",
        max_response_chars=context.config.limits.max_model_response_chars,
    )
    required_payload = _required_modules_payload(context, hashes.required_order)
    imported_payload = [spec.imported_context_ir() for spec in resolve_imported_specs(context)]

    output = [
        f"RENDER {module}",
        f"- Renderer: {renderer.name} ({renderer_provider(context)})",
        f"- Reason: {plan.reason}",
        f"- Range: {context.spec.functional_units[plan.start_index].id}:"
        f"{context.spec.functional_units[plan.end_index].id}",
    ]

    for index in range(plan.start_index, plan.end_index + 1):
        unit = context.spec.functional_units[index]
        append_render_log(context.generated_dir, f"start {unit.id}: {unit.title}")
        try:
            render_one_unit(
                context,
                metadata,
                index,
                renderer,
                hashes,
                required_payload,
                imported_payload,
                adapter,
                budget,
            )
        except MintError as exc:
            output.append(f"- FAILED {unit.id}: {unit.title}")
            return 1, "\n".join(output) + "\n\n" + str(exc) + "\n"
        output.append(f"- Completed {unit.id}: {unit.title}")

    latest_metadata = load_metadata(context.generated_dir) or metadata
    write_run_report(context, build_run_report(context, latest_metadata))
    return 0, "\n".join(output) + "\n"


def clean_module(module: str, yes: bool = False) -> tuple[int, str]:
    context = load_context(module)
    if not yes:
        return 1, "Refusing to clean without --yes. Re-run: mint clean " + module + " --yes\n"
    removed: list[str] = []
    for path in [context.generated_dir, context.conformance_dir]:
        if path.exists():
            shutil.rmtree(path)
            removed.append(path.relative_to(context.root).as_posix())
    if not removed:
        return 0, f"CLEAN {module}\n- Nothing to remove.\n"
    return 0, f"CLEAN {module}\n" + "".join(f"- Removed {path}\n" for path in removed)


def inspect_unit(module: str, unit_id: str) -> tuple[int, str]:
    context = load_context(module)
    unit = find_unit(context.spec, unit_id)
    metadata = load_metadata(context.generated_dir)
    record = record_by_unit(metadata or {}).get(unit_id)

    lines = [
        f"Unit: {unit.id}",
        f"Title: {unit.title}",
        "Spec:",
        *[f"- {item}" for item in unit.spec],
        "Acceptance:",
        *[f"- {item}" for item in unit.acceptance],
    ]

    if record:
        lines.extend(
            [
                "Record:",
                f"- status: {record.get('status')}",
                f"- textHash: {record.get('textHash')}",
                f"- beforeCommit: {record.get('beforeCommit')}",
                f"- finishedCommit: {record.get('finishedCommit')}",
                f"- attempts: {record.get('attempts')}",
                f"- testQuality: {record.get('testQuality', {}).get('status', 'none')}",
            ]
        )
    else:
        lines.append("Record: none")

    attempts_dir = context.generated_dir / ".mintgen" / "attempts" / unit_id
    if attempts_dir.exists():
        lines.append("Attempts:")
        for path in sorted(attempts_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            lines.append(
                f"- {path.name}: {data.get('phase')} attempt={data.get('attempt')} "
                f"renderer={data.get('renderer')} exit={data.get('exitCode')} "
                f"[{data.get('classification')}] {data.get('summary')}"
            )
    else:
        lines.append("Attempts: none")
    return 0, "\n".join(lines) + "\n"


def determine_render_plan(
    context: ModuleContext,
    metadata: dict[str, Any] | None,
    from_unit: str | None,
    unit_range: str | None,
    force: bool,
    hashes: ContextHashes,
) -> RenderPlan:
    if from_unit and unit_range:
        raise MintError("Use either --from or --range, not both.")

    end_index = len(context.spec.functional_units) - 1
    if unit_range:
        start_unit, end_unit = parse_unit_range(unit_range)
        return RenderPlan(
            unit_index(context.spec, start_unit),
            unit_index(context.spec, end_unit),
            "explicit range",
        )
    if from_unit:
        return RenderPlan(unit_index(context.spec, from_unit), end_index, "explicit --from")
    if force:
        return RenderPlan(0, end_index, "forced render")
    if metadata is None:
        return RenderPlan(0, end_index, "no generated metadata")
    if metadata.get("nonFunctionalSpecHash") != context.spec.non_functional_hash:
        return RenderPlan(0, end_index, "non-functional spec changed")
    if metadata.get("importedContextHash") != hashes.imported_context_hash:
        return RenderPlan(0, end_index, "imported context changed")
    if metadata.get("requiredModuleCodeHash") != hashes.required_module_code_hash:
        return RenderPlan(0, end_index, "required module code changed")

    records = record_by_unit(metadata)
    for index, unit in enumerate(context.spec.functional_units):
        record = records.get(unit.id)
        if record is None:
            return RenderPlan(index, end_index, f"new functional unit {unit.id}")
        if record.get("textHash") != unit_text_hash(unit):
            return RenderPlan(index, end_index, f"functional unit changed: {unit.id}")
        if record.get("status") != "passed":
            return RenderPlan(index, end_index, f"incomplete functional unit: {unit.id}")

    if len(records) != len(context.spec.functional_units):
        return RenderPlan(0, end_index, "functional unit set changed")
    return RenderPlan(0, end_index, "already rendered", noop=True)


def prepare_render_workspace(
    context: ModuleContext,
    metadata: dict[str, Any] | None,
    plan: RenderPlan,
    hashes: ContextHashes,
) -> None:
    if plan.start_index == 0 or metadata is None or not context.generated_dir.exists():
        shutil.rmtree(context.generated_dir, ignore_errors=True)
        shutil.rmtree(context.conformance_dir, ignore_errors=True)
        return

    start_unit = context.spec.functional_units[plan.start_index]
    records = record_by_unit(metadata)
    checkpoint = records.get(start_unit.id, {}).get("beforeCommit")
    if checkpoint is None and plan.start_index > 0:
        previous_unit = context.spec.functional_units[plan.start_index - 1]
        checkpoint = records.get(previous_unit.id, {}).get("finishedCommit")

    if checkpoint is None:
        raise MintError(
            f"Cannot rerender from {start_unit.id}: no checkpoint recorded. "
            f"Fix: run a full render with --force."
        )
    reset_hard(context.generated_dir, str(checkpoint))

    for unit in context.spec.functional_units[plan.start_index :]:
        shutil.rmtree(context.conformance_dir / unit.id, ignore_errors=True)

    keep_ids = {unit.id for unit in context.spec.functional_units[: plan.start_index]}
    trim_records(metadata, keep_ids)
    refresh_metadata_hashes(
        metadata,
        context.spec,
        context.generated_dir,
        imported_context_hash=hashes.imported_context_hash,
        required_module_code_hash=hashes.required_module_code_hash,
    )
    write_metadata(context.generated_dir, metadata)


def render_one_unit(
    context: ModuleContext,
    metadata: dict[str, Any],
    index: int,
    renderer: Renderer,
    hashes: ContextHashes,
    required_payload: list[dict[str, Any]],
    imported_payload: list[dict[str, Any]],
    adapter: StackAdapter,
    budget: BudgetTracker,
) -> None:
    unit = context.spec.functional_units[index]
    before_commit = git_head(context.generated_dir)
    if before_commit is None:
        raise MintError(f"Generated repo has no checkpoint before {unit.id}")

    started_at = now_iso()
    units_so_far = [u.to_dict() for u in context.spec.functional_units[: index + 1]]

    def make_request(phase: str, attempt: int, feedback: str | None) -> RenderRequest:
        return RenderRequest(
            module=context.module,
            stack=context.spec.stack,
            template=context.spec.template,
            spec_ir=context.spec.to_ir(),
            definitions=[{"name": d.name, "text": d.text} for d in context.spec.definitions],
            implementation=context.spec.implementation,
            test=context.spec.test,
            imported_context=imported_payload,
            required_modules=required_payload,
            units_so_far=units_so_far,
            current_unit=unit.to_dict(),
            phase=phase,
            attempt=attempt,
            feedback=feedback,
            prompt_hints=adapter.prompt_hints(context, hashes.required_order),
            code_fence_language=adapter.code_fence_language,
        )

    # ----- implementation + unit-test phase (one retry on failure) -----
    unit_retries = context.config.limits.unit_retries
    attempt = 1
    feedback: str | None = None
    while True:
        try:
            outcome, patch = render_validated_patch(
                renderer, make_request("unit", attempt, feedback)
            )
        except PatchAttemptFailure as exc:
            write_patch_failure_attempt(context, unit, "unit", attempt, exc)
            check_budget(context, budget, unit, "unit", attempt, prompt=exc.prompt, response=exc.response)
            if attempt > unit_retries:
                append_render_log(context.generated_dir, f"unit patch invalid {unit.id}")
                raise MintError(format_patch_failure("unit", unit, exc))
            feedback = patch_feedback(exc)
            attempt += 1
            continue
        apply_patch(patch, context.generated_dir, context.conformance_dir)

        result = adapter.run_unit_tests(context, required_order=hashes.required_order)
        classification = adapter.classify_test_result("unit", result)
        write_attempt(
            context.generated_dir,
            unit.id,
            "unit",
            attempt,
            script=adapter.unit_command_label,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            classification=classification,
            summary=patch.get("summary", ""),
            prompt=outcome.prompt,
            response=outcome.response,
            patch=patch,
            renderer=outcome.renderer,
            cassette_id=outcome.cassette_id,
        )
        check_budget(
            context,
            budget,
            unit,
            "unit",
            attempt,
            prompt=outcome.prompt,
            response=outcome.response,
        )
        if classification == "passed":
            break
        if attempt > unit_retries:
            append_render_log(context.generated_dir, f"unit failed {unit.id} ({classification})")
            raise MintError(format_phase_failure("unit", unit, classification, result))
        feedback = combined_output(result)
        attempt += 1
    unit_attempts = attempt

    # ----- conformance phase (one retry: re-render with conformance feedback) -----
    conformance_retries = context.config.limits.conformance_retries
    attempt = 1
    feedback = None
    conformance_render_attempts = 0
    while True:
        if attempt > 1:
            conformance_render_attempts += 1
            try:
                outcome, patch = render_validated_patch(
                    renderer, make_request("conformance", attempt, feedback)
                )
            except PatchAttemptFailure as exc:
                write_patch_failure_attempt(context, unit, "conformance", attempt, exc)
                check_budget(
                    context,
                    budget,
                    unit,
                    "conformance",
                    attempt,
                    prompt=exc.prompt,
                    response=exc.response,
                )
                if attempt > conformance_retries:
                    append_render_log(context.generated_dir, f"conformance patch invalid {unit.id}")
                    raise MintError(format_patch_failure("conformance", unit, exc))
                feedback = patch_feedback(exc)
                attempt += 1
                continue
            apply_patch(patch, context.generated_dir, context.conformance_dir)
            # Guard: a conformance-driven change must not break the unit tests.
            recheck = adapter.run_unit_tests(context, required_order=hashes.required_order)
            if adapter.classify_test_result("unit", recheck) != "passed":
                raise MintError(
                    format_phase_failure("unit-regression", unit, "unit_failed", recheck)
                )

        result = adapter.run_conformance_tests(context, required_order=hashes.required_order)
        classification = adapter.classify_test_result("conformance", result)
        write_attempt(
            context.generated_dir,
            unit.id,
            "conformance",
            attempt,
            script=adapter.conformance_command_label,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            classification=classification,
            summary=f"conformance {unit.id} attempt {attempt}",
            prompt=outcome.prompt if attempt > 1 else None,
            response=outcome.response if attempt > 1 else None,
            patch=patch if attempt > 1 else None,
            renderer=outcome.renderer if attempt > 1 else None,
            cassette_id=outcome.cassette_id if attempt > 1 else None,
        )
        check_budget(
            context,
            budget,
            unit,
            "conformance",
            attempt,
            prompt=outcome.prompt if attempt > 1 else None,
            response=outcome.response if attempt > 1 else None,
        )
        if classification == "passed":
            break
        if attempt > conformance_retries:
            append_render_log(
                context.generated_dir, f"conformance failed {unit.id} ({classification})"
            )
            raise MintError(format_phase_failure("conformance", unit, classification, result))
        feedback = combined_output(result)
        attempt += 1

    adapter.cleanup_runtime_caches(context.generated_dir, context.conformance_dir)

    test_quality = evaluate_test_quality(
        context,
        unit,
        required_src=adapter.required_runtime_paths(context, hashes.required_order),
        baseline_already_passed=True,
    )
    test_quality_output = format_test_quality_verdict(test_quality)
    test_quality_classification = (
        "passed" if test_quality.get("status") in {"passed", "skipped"} else "test_quality_failed"
    )
    write_attempt(
        context.generated_dir,
        unit.id,
        "test-quality",
        1,
        script="mint internal test-quality",
        exit_code=0 if test_quality_classification == "passed" else 1,
        stdout=test_quality_output,
        stderr="",
        classification=test_quality_classification,
        summary=f"test-quality {unit.id}: {test_quality.get('status')}",
        renderer=outcome.renderer,
        extra={"testQuality": test_quality},
    )
    check_budget(context, budget, unit, "test-quality", 1, prompt=None, response=None)
    if test_quality_classification != "passed":
        append_render_log(context.generated_dir, f"test-quality failed {unit.id}")
        record = {
            "id": unit.id,
            "title": unit.title,
            "textHash": unit_text_hash(unit),
            "status": "test_quality_failed",
            "startedAt": started_at,
            "finishedAt": now_iso(),
            "beforeCommit": before_commit,
            "implementationCommit": None,
            "unitTestsCommit": None,
            "conformanceCommit": None,
            "finishedCommit": None,
            "renderer": outcome.renderer,
            "attempts": {
                "implementation": unit_attempts,
                "unit": unit_attempts,
                "conformance": conformance_render_attempts + 1,
                "testQuality": 1,
            },
            "testQuality": test_quality,
        }
        replace_record(metadata, record)
        refresh_metadata_hashes(
            metadata,
            context.spec,
            context.generated_dir,
            imported_context_hash=hashes.imported_context_hash,
            required_module_code_hash=hashes.required_module_code_hash,
        )
        write_metadata(context.generated_dir, metadata)
        raise MintError(format_test_quality_failure(unit, test_quality_output))

    record = {
        "id": unit.id,
        "title": unit.title,
        "textHash": unit_text_hash(unit),
        "status": "passed",
        "startedAt": started_at,
        "finishedAt": now_iso(),
        "beforeCommit": before_commit,
        "implementationCommit": None,
        "unitTestsCommit": None,
        "conformanceCommit": None,
        "finishedCommit": None,
        "renderer": outcome.renderer,
        "attempts": {
            "implementation": unit_attempts,
            "unit": unit_attempts,
            "conformance": conformance_render_attempts + 1,
            "testQuality": 1,
        },
        "testQuality": test_quality,
    }
    replace_record(metadata, record)
    metadata["lastSuccessfulUnitId"] = unit.id
    refresh_metadata_hashes(
        metadata,
        context.spec,
        context.generated_dir,
        imported_context_hash=hashes.imported_context_hash,
        required_module_code_hash=hashes.required_module_code_hash,
    )
    write_metadata(context.generated_dir, metadata)
    append_render_log(context.generated_dir, f"completed code {unit.id}")

    body = commit_body(context, metadata, unit)
    code_commit = commit_all(context.generated_dir, f"[mint] completed {unit.id}: {unit.title}", body)
    record["implementationCommit"] = code_commit
    record["unitTestsCommit"] = code_commit
    record["conformanceCommit"] = code_commit
    record["finishedCommit"] = code_commit
    replace_record(metadata, record)
    refresh_metadata_hashes(
        metadata,
        context.spec,
        context.generated_dir,
        imported_context_hash=hashes.imported_context_hash,
        required_module_code_hash=hashes.required_module_code_hash,
    )
    write_metadata(context.generated_dir, metadata)
    append_render_log(context.generated_dir, f"metadata {unit.id} {code_commit}")
    commit_all(context.generated_dir, f"[mint] metadata {unit.id}: {unit.title}", body)


def render_validated_patch(
    renderer: Renderer,
    request: RenderRequest,
) -> tuple[Any, dict[str, Any]]:
    try:
        outcome = renderer.render(request)
    except ModelOutputError as exc:
        raise PatchAttemptFailure(
            str(exc),
            prompt=exc.prompt,
            response=exc.response,
            renderer=exc.renderer,
            cassette_id=exc.cassette_id,
        ) from exc

    try:
        patch = validate_patch(outcome.patch)
    except MintError as exc:
        raise PatchAttemptFailure(
            f"Renderer patch failed schema validation: {exc}",
            prompt=outcome.prompt,
            response=outcome.response,
            patch=outcome.patch,
            renderer=outcome.renderer,
            cassette_id=outcome.cassette_id,
        ) from exc
    return outcome, patch


def write_patch_failure_attempt(
    context: ModuleContext,
    unit: FunctionalUnit,
    phase: str,
    attempt: int,
    failure: PatchAttemptFailure,
) -> None:
    write_attempt(
        context.generated_dir,
        unit.id,
        phase,
        attempt,
        script="mint internal patch validation",
        exit_code=1,
        stdout="",
        stderr=failure.message,
        classification="patch_invalid",
        summary=f"{phase} patch invalid: {failure.message}",
        prompt=failure.prompt,
        response=failure.response,
        patch=failure.patch,
        renderer=failure.renderer,
        cassette_id=failure.cassette_id,
        extra={"patchValidationError": failure.message},
    )


def check_budget(
    context: ModuleContext,
    budget: BudgetTracker,
    unit: FunctionalUnit,
    phase: str,
    attempt: int,
    *,
    prompt: str | None,
    response: str | None,
) -> None:
    budget.record(prompt=prompt, response=response)
    reason = budget.exceeded()
    if reason is None:
        return
    report_path = write_budget_abort_report(context, budget, unit, phase, attempt, reason)
    raise MintError(
        f"Render budget exceeded for {context.module} at {unit.id} {phase} attempt {attempt}: "
        f"{reason}.\n"
        f"Budget report: {report_path.relative_to(context.root).as_posix()}\n"
        "Fix: increase limits.maxRenderAttempts or limits.maxRenderTokensEstimate in mint.yaml, "
        "or render a smaller range."
    )


def write_budget_abort_report(
    context: ModuleContext,
    budget: BudgetTracker,
    unit: FunctionalUnit,
    phase: str,
    attempt: int,
    reason: str,
) -> Path:
    reports_dir = context.generated_dir / ".mintgen" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "budget-abort.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "module": context.module,
                "unitId": unit.id,
                "phase": phase,
                "attempt": attempt,
                "reason": reason,
                "attempts": budget.attempts,
                "maxAttempts": budget.max_attempts,
                "tokensEstimate": budget.tokens_estimate,
                "maxTokensEstimate": budget.max_tokens_estimate,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def patch_feedback(failure: PatchAttemptFailure) -> str:
    return (
        "Your previous response did not satisfy the renderer patch contract.\n"
        f"{failure.message}\n"
        "Return only one JSON object with a non-empty files list. Each file entry "
        "must include a relative path, action write|delete, root module|conformance, "
        "and string contents for write actions."
    )


def _required_modules_payload(
    context: ModuleContext, required_order: tuple[str, ...]
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for module in required_order:
        spec = parse_spec_file(context.spec_path(module))
        adapter = adapter_for_stack(spec.stack)
        module_dir = context.generated_dir_for(module)
        files: list[dict[str, str]] = []
        for path in adapter.required_payload_files(module_dir):
            files.append(
                {
                    "path": path.relative_to(module_dir).as_posix(),
                    "contents": path.read_text(encoding="utf-8"),
                    "language": adapter.code_fence_language,
                }
            )
        payload.append({"module": module, "files": files})
    return payload


def classify_test_result(phase: str, returncode: int) -> str:
    if returncode == 0:
        return "passed"
    if returncode == 5:
        return "no_tests"
    return f"{phase}_failed"


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(result.stderr.rstrip())
    return "\n".join(parts)


def remove_runtime_caches(path: Path) -> None:
    if not path.exists():
        return
    for cache_dir in path.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in path.rglob(".pytest_cache"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in path.rglob(".vite"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in path.rglob(".vitest"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in path.rglob("coverage"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in path.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)
    for tsbuildinfo in path.rglob("*.tsbuildinfo"):
        tsbuildinfo.unlink(missing_ok=True)


def run_script(
    context: ModuleContext,
    script: str,
    *,
    required_src: list[Path] | None = None,
) -> subprocess.CompletedProcess[str]:
    path = context.root / script
    if not path.exists():
        raise MintError(
            f"Configured script not found: {script}. "
            f"Fix: run mint doctor; {_missing_file_fix()}."
        )
    if not os.access(path, os.X_OK):
        raise MintError(f"Configured script is not executable: {script}. Fix: chmod +x {script}.")
    env = os.environ.copy()
    env["MINT_GENERATED_DIR"] = str(context.generated_dir)
    env["MINT_CONFORMANCE_DIR"] = str(context.conformance_dir)
    if required_src:
        env["MINT_REQUIRED_SRC"] = os.pathsep.join(str(p) for p in required_src)
    # Never write .pyc during generated test runs. Regeneration can overwrite a file
    # with same-size content within the same second; Python's mtime+size pyc cache
    # would then serve STALE bytecode from a previous attempt and mask a real fix (or
    # a real break). Disabling bytecode entirely removes that whole class of bug and
    # keeps caches out of checkpoints for free.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/mint-pycache")
    env.setdefault("PYTHON_BIN", sys.executable)
    return subprocess.run(
        [str(path), context.module],
        cwd=context.root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def replace_record(metadata: dict[str, Any], record: dict[str, Any]) -> None:
    records = [
        existing
        for existing in metadata.get("functionalUnits", [])
        if existing.get("id") != record["id"]
    ]
    records.append(record)
    metadata["functionalUnits"] = sorted(records, key=lambda item: unit_sort_key(str(item["id"])))


def unit_sort_key(unit_id: str) -> tuple[int, str]:
    number = unit_id[2:] if unit_id.startswith("FR") else ""
    return (int(number) if number.isdigit() else 999999, unit_id)


def parse_unit_range(unit_range: str) -> tuple[str, str]:
    if ":" not in unit_range:
        raise MintError("--range must look like FR1:FR3")
    start, end = unit_range.split(":", 1)
    start = start.strip()
    end = end.strip()
    if unit_sort_key(start) > unit_sort_key(end):
        raise MintError("--range start must be before or equal to range end")
    return start, end


def unit_index(spec: Spec, unit_id: str) -> int:
    for index, unit in enumerate(spec.functional_units):
        if unit.id == unit_id:
            return index
    known = ", ".join(unit.id for unit in spec.functional_units)
    raise MintError(f"Unknown functional unit: {unit_id}. Known units: {known}")


def find_unit(spec: Spec, unit_id: str) -> FunctionalUnit:
    return spec.functional_units[unit_index(spec, unit_id)]


def format_script_failure(phase: str, result: subprocess.CompletedProcess[str]) -> str:
    lines = [f"{phase} script failed with exit code {result.returncode}."]
    if result.stdout.strip():
        lines.append("stdout:")
        lines.append(result.stdout.rstrip())
    if result.stderr.strip():
        lines.append("stderr:")
        lines.append(result.stderr.rstrip())
    return "\n".join(lines) + "\n"


def format_phase_failure(
    phase: str,
    unit: FunctionalUnit,
    classification: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    if classification == "no_tests":
        head = (
            f"No tests were discovered for {unit.id} ({phase}). "
            f"Every unit must ship tests; a passing gate with zero tests is not allowed."
        )
    else:
        head = f"{phase} tests failed for {unit.id}: {unit.title} (exit {result.returncode})."
    body = combined_output(result)
    return head + ("\n" + body if body else "") + "\n"


def format_patch_failure(
    phase: str,
    unit: FunctionalUnit,
    failure: PatchAttemptFailure,
) -> str:
    return (
        f"{phase} renderer patch was invalid for {unit.id}: {unit.title}.\n"
        f"{failure.message}\n"
        "The validation error was fed back to the renderer while retries remained. "
        "Fix: return a JSON patch matching the renderer contract.\n"
    )


def format_test_quality_failure(unit: FunctionalUnit, verdict_output: str) -> str:
    return (
        f"test-quality gate failed for {unit.id}: {unit.title}.\n"
        + verdict_output
        + "Fix: strengthen generated tests so acceptance criteria are referenced, "
        "coverage meets the configured threshold, and mutation probes fail.\n"
    )


def commit_body(context: ModuleContext, metadata: dict[str, Any], unit: FunctionalUnit) -> str:
    return (
        f"Module: {context.module}\n"
        f"Unit: {unit.id}\n"
        f"Render-Id: {metadata.get('renderId')}\n"
        f"Provider: {metadata.get('provider')}\n"
        f"Prompt-Version: {metadata.get('promptVersion')}\n"
        f"Model: {metadata.get('model')}\n"
    )
