"""The render loop: unit and conformance phases, gates, retries, budgets, and commits."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from .. import progress
from ..config import load_config
from ..errors import MintError
from ..gitutil import (
    clean_untracked,
    commit_all,
    ensure_git_repo,
    git_head,
    reset_hard,
)
from ..modgraph import build_render_order
from ..renderer import (
    RenderOutcome,
    RenderRequest,
    apply_patch,
    get_renderer,
    is_anthropic_live_provider,
    is_model_provider,
    validate_patch,
)
from ..renderer.base import Renderer
from ..renderer.model import ModelClient, ModelOutputError
from ..specs import FunctionalUnit
from ..state import (
    append_render_log,
    fresh_metadata,
    load_metadata,
    module_render_lock,
    now_iso,
    record_by_unit,
    refresh_metadata_hashes,
    trim_records,
    unit_text_hash,
    write_attempt,
    write_metadata,
)
from ..stacks import StackAdapter
from ..test_quality import evaluate_test_quality, format_test_quality_verdict
from .context import BudgetTracker, ContextHashes, ModuleContext, PatchAttemptFailure, RenderPlan, _module_files_payload, _module_interface_files, _required_modules_payload, _spec_loader, _unit_resources_payload, compute_context_hashes, load_context, renderer_model, renderer_prompt_version, renderer_provider, resolve_imported_specs, unit_sort_key
from .health import healthcheck_module
from .interface import interface_change_lines, public_literal_warnings
from .planning import determine_render_plan
from .reports import build_run_report, write_run_report
from .stylelock import format_style_lock_failure, style_lock_prompt_hints, style_lock_violations


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


def render_all(
    *,
    force: bool = False,
    model_client: ModelClient | None = None,
    root: Path | None = None,
) -> tuple[int, str]:
    """Render every spec in the project in dependency order (``mint render --all``).

    Each module gets its own attempt/token budget, exactly as if it had been
    rendered by name. The first failing module stops the run.
    """
    root = (root or Path.cwd()).resolve()
    config = load_config(root / "mint.yaml")
    spec_paths = sorted((root / config.specs_dir).glob("*.mint.md"))
    if not spec_paths:
        raise MintError(
            f"No specs found under {config.specs_dir}/. Fix: mint new <module> ..."
        )
    loader = _spec_loader(root, config.specs_dir)
    order: list[str] = []
    for path in spec_paths:
        for module in build_render_order(path.name[: -len(".mint.md")], loader):
            if module not in order:
                order.append(module)

    sections: list[str] = []
    for module in order:
        status, output = render_single_module(
            module, None, None, force, model_client=model_client, root=root
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
    # Hold a per-module lock across the whole render so two concurrent renders of
    # the same module can't interleave checkpoint reset / cleanup / commit and
    # corrupt state. The lock keys on the module's generated dir, so a module
    # rendered both standalone and as another module's dependency still serializes.
    context = load_context(module, root)
    with module_render_lock(context.generated_dir):
        return _render_single_module_locked(
            context,
            from_unit,
            unit_range,
            force,
            model_client=model_client,
            budget=budget,
        )


def _render_single_module_locked(
    context: ModuleContext,
    from_unit: str | None,
    unit_range: str | None,
    force: bool,
    *,
    model_client: ModelClient | None,
    budget: BudgetTracker | None = None,
) -> tuple[int, str]:
    module = context.module
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

    hashes = compute_context_hashes(context)
    metadata = load_metadata(context.generated_dir)
    plan = determine_render_plan(context, metadata, from_unit, unit_range, force, hashes)
    if plan.noop:
        progress.report(f"{module}: up to date (NOOP)")
        if metadata is not None:
            write_run_report(context, build_run_report(context, metadata))
        return 0, f"NOOP {module}\n- Generated output already matches spec and inputs.\n"

    # Captured before the workspace reset: the public interface of the last
    # successful render, so a completed render can report exactly what changed
    # for dependents. None on a fresh render (nothing to compare against).
    interface_before = _module_interface_files(context) if metadata is not None else None

    # Only shell out to the prepare script once we know we will actually render;
    # a NOOP must not run per-module prepare for every module in the chain.
    prepare = adapter.prepare(context)
    if prepare.returncode != 0:
        return 1, format_script_failure("prepare", prepare)

    _enforce_max_units_per_render(context, plan)
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

    progress.report(
        f"{module}: render {context.spec.functional_units[plan.start_index].id}:"
        f"{context.spec.functional_units[plan.end_index].id} via {renderer.name} ({plan.reason})"
    )
    for index in range(plan.start_index, plan.end_index + 1):
        unit = context.spec.functional_units[index]
        append_render_log(context.generated_dir, f"start {unit.id}: {unit.title}")
        progress.report(f"{module} {unit.id}: {unit.title}")
        unit_started = time.monotonic()
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
            progress.report(f"{module} {unit.id}: FAILED after {time.monotonic() - unit_started:.1f}s")
            output.append(f"- FAILED {unit.id}: {unit.title}")
            return 1, "\n".join(output) + "\n\n" + str(exc) + "\n"
        progress.report(f"{module} {unit.id}: completed in {time.monotonic() - unit_started:.1f}s")
        output.append(f"- Completed {unit.id}: {unit.title}")

    interface_after = _module_interface_files(context)
    if interface_before is not None:
        output.extend(interface_change_lines(context, interface_before, interface_after))
    output.extend(public_literal_warnings(interface_after))

    latest_metadata = load_metadata(context.generated_dir) or metadata
    write_run_report(context, build_run_report(context, latest_metadata))
    return 0, "\n".join(output) + "\n"


def _enforce_max_units_per_render(context: ModuleContext, plan: RenderPlan) -> None:
    """Cap how many functional units a single render may produce.

    ``limits.maxFunctionalUnitsPerRender`` is a required config key but nothing
    enforced it, so a huge spec would render unbounded units in one pass. Refuse
    loudly and point the user at rendering a smaller range.
    """
    limit = context.config.limits.max_functional_units_per_render
    if limit <= 0:
        return
    count = plan.end_index - plan.start_index + 1
    if count <= limit:
        return
    start_unit = context.spec.functional_units[plan.start_index].id
    end_unit = context.spec.functional_units[plan.end_index].id
    raise MintError(
        f"Render of {context.module} spans {count} functional units "
        f"({start_unit}:{end_unit}) but limits.maxFunctionalUnitsPerRender is {limit}. "
        "Fix: render a smaller --range (e.g. one slice at a time) or raise "
        "limits.maxFunctionalUnitsPerRender in mint.yaml."
    )


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
    # reset --hard only restores tracked files; untracked files an aborted prior
    # attempt left behind survive and can make this attempt pass spuriously (then
    # get committed by commit_all's `add -A`). Wipe them for a pristine tree.
    clean_untracked(context.generated_dir)

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

    prompt_hints = adapter.prompt_hints(context, hashes.required_order)
    if context.spec.style_lock is not None:
        prompt_hints = prompt_hints + style_lock_prompt_hints(context.spec.style_lock)
    unit_resources = _unit_resources_payload(context, unit)

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
            prompt_hints=prompt_hints,
            code_fence_language=adapter.code_fence_language,
            module_files_so_far=_module_files_payload(context),
            unit_resources=unit_resources,
        )

    # ----- implementation + unit-test phase (one retry on failure) -----
    unit_retries = context.config.limits.unit_retries
    attempt = 1
    feedback: str | None = None
    while True:
        progress.report(
            f"{context.module} {unit.id}: unit render attempt {attempt} ({renderer.name})"
        )
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
        _enforce_unit_scoped_conformance(unit, patch)
        apply_patch(patch, context.generated_dir, context.conformance_dir)

        feedback = _style_lock_gate(
            context, unit, "unit", attempt, unit_retries, outcome, patch, budget
        )
        if feedback is not None:
            attempt += 1
            continue

        rendered_unit_ids = tuple(u["id"] for u in units_so_far)
        progress.report(f"{context.module} {unit.id}: unit tests (attempt {attempt})")
        result = adapter.run_unit_tests(
            context,
            required_order=hashes.required_order,
            rendered_unit_ids=rendered_unit_ids,
        )
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
        feedback = _truncate_feedback(combined_output(result))
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
            progress.report(
                f"{context.module} {unit.id}: conformance re-render attempt {attempt} "
                f"({renderer.name})"
            )
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
            _enforce_unit_scoped_conformance(unit, patch)
            apply_patch(patch, context.generated_dir, context.conformance_dir)
            # Guard: a conformance-driven change must not break the unit tests.
            recheck = adapter.run_unit_tests(
                context,
                required_order=hashes.required_order,
                rendered_unit_ids=tuple(u["id"] for u in units_so_far),
            )
            if adapter.classify_test_result("unit", recheck) != "passed":
                write_attempt(
                    context.generated_dir,
                    unit.id,
                    "unit-regression",
                    attempt,
                    script=adapter.unit_command_label,
                    exit_code=recheck.returncode,
                    stdout=recheck.stdout,
                    stderr=recheck.stderr,
                    classification="unit_failed",
                    summary=(
                        f"conformance re-render regressed unit tests for {unit.id} "
                        f"attempt {attempt}"
                    ),
                    renderer=outcome.renderer,
                )
                append_render_log(
                    context.generated_dir,
                    f"unit regression {unit.id} (conformance attempt {attempt})",
                )
                raise MintError(
                    format_phase_failure("unit-regression", unit, "unit_failed", recheck)
                )
            feedback = _style_lock_gate(
                context,
                unit,
                "conformance",
                attempt,
                conformance_retries,
                outcome,
                patch,
                budget,
            )
            if feedback is not None:
                attempt += 1
                continue

        progress.report(f"{context.module} {unit.id}: conformance tests (attempt {attempt})")
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
        feedback = _truncate_feedback(combined_output(result))
        attempt += 1

    adapter.cleanup_runtime_caches(context.generated_dir, context.conformance_dir)

    progress.report(f"{context.module} {unit.id}: test-quality gate")
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
            "textHash": unit_text_hash(unit, context.root),
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
        "textHash": unit_text_hash(unit, context.root),
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


def _style_lock_gate(
    context: ModuleContext,
    unit: FunctionalUnit,
    phase: str,
    attempt: int,
    retries: int,
    outcome: RenderOutcome,
    patch: dict[str, Any],
    budget: BudgetTracker,
) -> str | None:
    """Scan the freshly applied patch against the spec's style lock.

    Returns ``None`` when the module has no lock or the sources are clean. On a
    violation the attempt is recorded and charged to the budget; the failure
    message comes back as feedback for the next render attempt, or raises once
    ``retries`` is exhausted. Shared by the unit and conformance phases so both
    enforce the lock identically.
    """
    if context.spec.style_lock is None:
        return None
    violations = style_lock_violations(context.generated_dir / "src", context.spec.style_lock)
    if not violations:
        return None
    message = format_style_lock_failure(unit, violations)
    where = f"{unit.id} attempt {attempt}" if phase == "unit" else f"{unit.id} {phase} attempt {attempt}"
    write_attempt(
        context.generated_dir,
        unit.id,
        "style-lock",
        attempt,
        script="mint internal style-lock scan",
        exit_code=1,
        stdout="\n".join(violations),
        stderr="",
        classification="style_lock_failed",
        summary=f"style lock violations in {where}",
        prompt=outcome.prompt,
        response=outcome.response,
        patch=patch,
        renderer=outcome.renderer,
        cassette_id=outcome.cassette_id,
    )
    check_budget(
        context, budget, unit, "style-lock", attempt, prompt=outcome.prompt, response=outcome.response
    )
    if attempt > retries:
        append_render_log(context.generated_dir, f"style lock failed {unit.id}")
        raise MintError(message)
    return message


def _enforce_unit_scoped_conformance(unit: FunctionalUnit, patch: dict[str, Any]) -> None:
    """Every conformance file a unit writes must live under ``<unit.id>/``.

    Incremental re-render only wipes ``conformance/<unit.id>/`` for the units it
    re-renders. If a patch writes a conformance file outside that subtree (e.g.
    ``shared_test.py`` at the conformance root), that file is never cleaned and
    keeps running against future renders forever. Enforce the documented
    unit-scoped contract loudly instead of silently accumulating stale tests.
    """
    prefix = f"{unit.id}/"
    offending = sorted(
        str(entry.get("path", ""))
        for entry in patch.get("files", [])
        if entry.get("root") == "conformance"
        and not str(entry.get("path", "")).replace("\\", "/").startswith(prefix)
    )
    if offending:
        raise MintError(
            f"Conformance patch for {unit.id} wrote outside conformance/{unit.id}/: "
            f"{', '.join(offending)}. "
            f"Every conformance file must live under {unit.id}/ so incremental "
            "re-renders can clean it. Fix: write conformance tests under "
            f"'{unit.id}/...' with root 'conformance'."
        )


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


FEEDBACK_MAX_CHARS = 16000


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(result.stderr.rstrip())
    return "\n".join(parts)


def _truncate_feedback(text: str) -> str:
    """Bound test-failure feedback before it is fed back to the renderer.

    Raw pytest/vitest output can be enormous; sending it verbatim blows the token
    budget or trips provider request-size limits. Keep the head and tail (the head
    shows the failing test, the tail usually carries the assertion/error) and drop
    the middle.
    """
    if len(text) <= FEEDBACK_MAX_CHARS:
        return text
    head = FEEDBACK_MAX_CHARS // 2
    tail = FEEDBACK_MAX_CHARS - head
    dropped = len(text) - FEEDBACK_MAX_CHARS
    return f"{text[:head]}\n...[truncated {dropped} characters of test output]...\n{text[-tail:]}"


def replace_record(metadata: dict[str, Any], record: dict[str, Any]) -> None:
    records = [
        existing
        for existing in metadata.get("functionalUnits", [])
        if existing.get("id") != record["id"]
    ]
    records.append(record)
    metadata["functionalUnits"] = sorted(records, key=lambda item: unit_sort_key(str(item["id"])))


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
