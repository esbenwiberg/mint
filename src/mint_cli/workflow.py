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

from .config import MintConfig, load_config
from .errors import MintError
from .gitutil import commit_all, ensure_git_repo, git_head, reset_hard
from .hashing import hash_generated_files, hash_json
from .modgraph import build_render_order
from .renderer import (
    RenderRequest,
    apply_patch,
    get_renderer,
    validate_patch,
)
from .renderer.base import Renderer
from .renderer.model import ModelClient, ModelOutputError
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
from .test_quality import evaluate_test_quality, format_test_quality_verdict

# pytest exit code 5 means "no tests were collected".
PYTEST_NO_TESTS = 5


@dataclass(frozen=True)
class ModuleContext:
    root: Path
    module: str
    config: MintConfig
    spec: Spec
    generated_dir: Path
    conformance_dir: Path

    def spec_path(self, module: str) -> Path:
        return self.root / "specs" / f"{module}.mint.md"

    def generated_dir_for(self, module: str) -> Path:
        return self.root / self.config.generated_dir / module

    def src_dir_for(self, module: str) -> Path:
        return self.generated_dir_for(module) / "src"


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
    spec = parse_spec_file(root / "specs" / f"{module}.mint.md")
    if spec.module != module:
        raise MintError(
            f"Spec module '{spec.module}' does not match requested module '{module}'. "
            f"Fix: rename the spec file or the 'module' frontmatter key."
        )
    generated_dir = root / config.generated_dir / module
    conformance_dir = root / config.conformance_dir / module
    return ModuleContext(root, module, config, spec, generated_dir, conformance_dir)


def _spec_loader(root: Path):
    def load(module: str) -> Spec:
        return parse_spec_file(root / "specs" / f"{module}.mint.md")

    return load


def resolve_required_order(context: ModuleContext) -> list[str]:
    """Transitive required modules in dependency order, excluding the module itself."""
    order = build_render_order(context.module, _spec_loader(context.root))
    return [module for module in order if module != context.module]


def resolve_imported_specs(context: ModuleContext) -> list[Spec]:
    loader = _spec_loader(context.root)
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
    return context.spec.renderer_provider or context.config.renderer.provider


def renderer_model(context: ModuleContext) -> str:
    return context.spec.renderer_model or context.config.renderer.model


def renderer_prompt_version(context: ModuleContext) -> str:
    return context.spec.renderer_prompt_version or context.config.renderer.prompt_version


def parse_module(module: str) -> str:
    context = load_context(module)
    return json.dumps(context.spec.to_ir(), indent=2, sort_keys=True) + "\n"


def new_module(
    module: str,
    *,
    requires: list[str] | None = None,
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

    deps = requires or []
    spec_path = root / "specs" / f"{module}.mint.md"
    if spec_path.exists():
        return (
            1,
            f"FAIL new\n- Spec already exists: {spec_path.relative_to(root).as_posix()}\n",
        )

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(_starter_spec(module, deps), encoding="utf-8")
    return (
        0,
        "NEW "
        + module
        + "\n"
        + f"- Wrote {spec_path.relative_to(root).as_posix()}\n"
        + f"- Next: mint lint {module}\n",
    )


def lint_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    path = root / "specs" / f"{module}.mint.md"
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


def doctor_project(*, root: Path | None = None) -> tuple[int, str]:
    root = (root or Path.cwd()).resolve()
    failures: list[str] = []
    warnings: list[str] = []
    messages: list[str] = []

    try:
        config = load_config(root / "mint.yaml")
    except MintError as exc:
        return 1, f"FAIL doctor\n- {exc}\n- Fix: create mint.yaml or run from a mint project root.\n"

    messages.append(f"Config: {config.path.relative_to(root).as_posix()}")
    for label, script in [
        ("prepare", config.scripts.prepare),
        ("unit", config.scripts.unit),
        ("conformance", config.scripts.conformance),
    ]:
        script_path = root / script
        if not script_path.exists():
            failures.append(f"{label} script missing: {script} (fix: create the file)")
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

    spec_paths = sorted((root / "specs").glob("*.mint.md"))
    if not spec_paths:
        failures.append("No specs found under specs/ (fix: mint new <module>)")
    for spec_path in spec_paths:
        try:
            spec = parse_spec_file(spec_path)
        except MintError as exc:
            failures.append(str(exc))
            continue
        messages.append(f"Spec: {spec_path.relative_to(root).as_posix()}")
        for dep in sorted(set(spec.imports + spec.requires)):
            dep_path = root / "specs" / f"{dep}.mint.md"
            if not dep_path.exists():
                failures.append(
                    f"{spec.module} references missing spec {dep_path.relative_to(root).as_posix()}"
                )

    model_specs: list[str] = []
    for spec_path in spec_paths:
        try:
            spec = parse_spec_file(spec_path)
        except MintError:
            continue
        provider = spec.renderer_provider or config.renderer.provider
        if provider in {"model", "anthropic"}:
            model_specs.append(spec.module)

    if model_specs and os.environ.get("MINT_LIVE") != "1":
        cassette_root = root / "resources" / "cassettes"
        cassettes = list((cassette_root / "v1").glob("*.json"))
        if cassettes:
            messages.append(
                f"Replay cassettes: {len(cassettes)} in "
                f"{cassette_root.relative_to(root).as_posix()}"
            )
        else:
            failures.append(
                "Replay cassettes missing for model renderer specs "
                f"({', '.join(sorted(model_specs))}) "
                "(fix: MINT_LIVE=1 mint live-smoke <module>)"
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


def healthcheck_module(module: str, *, root: Path | None = None) -> tuple[int, str]:
    messages: list[str] = []
    failures: list[str] = []

    try:
        context = load_context(module, root)
        messages.append(f"Spec parsed: {len(context.spec.functional_units)} functional units")
    except MintError as exc:
        return 1, f"FAIL {module}\n- {exc}\n"

    messages.append(f"Config parsed: {context.config.path.relative_to(context.root).as_posix()}")

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

    for label, script in [
        ("Prepare script", context.config.scripts.prepare),
        ("Unit script", context.config.scripts.unit),
        ("Conformance script", context.config.scripts.conformance),
    ]:
        path = context.root / script
        if not path.exists():
            failures.append(f"{label} missing: {script}")
        elif not os.access(path, os.X_OK):
            failures.append(f"{label} is not executable: {script} (fix: chmod +x {script})")
        else:
            messages.append(f"{label}: {script}")

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
                failures.append(f"Generated repo is missing metadata: {context.generated_dir}")
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
    if provider not in {"model", "anthropic"}:
        return (
            1,
            f"FAIL live-smoke {module}\n"
            f"- {context.spec.path.relative_to(context.root).as_posix()} uses renderer "
            f"{provider!r}; live smoke requires rendererProvider: model or anthropic.\n",
        )
    if os.environ.get("MINT_LIVE") != "1":
        return (
            1,
            f"FAIL live-smoke {module}\n"
            "- MINT_LIVE=1 is required so live provider calls are always explicit.\n"
            f"- Next: MINT_LIVE=1 mint live-smoke {module}\n",
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
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


def _starter_spec(module: str, requires: list[str]) -> str:
    deps = _yaml_list(requires)
    return f"""---
module: {module}
description: {module} module
imports: {deps}
requires: {deps}
stack: python-lib
---

## definitions

- {module}: the public module being generated.

## implementation

- Use Python 3.12.
- Expose the public API under `src/{module.replace("-", "_")}/`.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call only the public API.
- Include at least one edge or error case before rendering real code.

## functional

- id: FR1
  title: Implement the first public behavior
  spec:
    - Replace this starter behavior with one concrete public capability.
  acceptance:
    - After calling the public API with a representative input, it returns the expected output.
"""


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
    order = build_render_order(module, _spec_loader(top.root))
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
    if budget is None:
        budget = BudgetTracker(
            max_attempts=context.config.limits.max_render_attempts,
            max_tokens_estimate=context.config.limits.max_render_tokens_estimate,
        )
    health_status, health_output = healthcheck_module(module, root=context.root)
    if health_status != 0:
        return health_status, health_output

    prepare = run_script(context, context.config.scripts.prepare)
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
    required_src = [context.src_dir_for(m) for m in hashes.required_order]
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
                required_src,
                imported_payload,
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
    required_src: list[Path],
    imported_payload: list[dict[str, Any]],
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

        result = run_script(context, context.config.scripts.unit, required_src=required_src)
        classification = classify_test_result("unit", result.returncode)
        write_attempt(
            context.generated_dir,
            unit.id,
            "unit",
            attempt,
            script=context.config.scripts.unit,
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
            recheck = run_script(context, context.config.scripts.unit, required_src=required_src)
            if classify_test_result("unit", recheck.returncode) != "passed":
                raise MintError(
                    format_phase_failure("unit-regression", unit, "unit_failed", recheck)
                )

        result = run_script(context, context.config.scripts.conformance, required_src=required_src)
        classification = classify_test_result("conformance", result.returncode)
        write_attempt(
            context.generated_dir,
            unit.id,
            "conformance",
            attempt,
            script=context.config.scripts.conformance,
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

    remove_runtime_caches(context.generated_dir)
    remove_runtime_caches(context.conformance_dir)

    test_quality = evaluate_test_quality(context, unit, required_src=required_src)
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
        src_dir = context.src_dir_for(module)
        files: list[dict[str, str]] = []
        if src_dir.exists():
            for path in sorted(src_dir.rglob("*.py")):
                files.append(
                    {
                        "path": path.relative_to(context.generated_dir_for(module)).as_posix(),
                        "contents": path.read_text(encoding="utf-8"),
                    }
                )
        payload.append({"module": module, "files": files})
    return payload


def classify_test_result(phase: str, returncode: int) -> str:
    if returncode == 0:
        return "passed"
    if returncode == PYTEST_NO_TESTS:
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
    for pyc in path.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)


def run_script(
    context: ModuleContext,
    script: str,
    *,
    required_src: list[Path] | None = None,
) -> subprocess.CompletedProcess[str]:
    path = context.root / script
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
