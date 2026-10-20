"""Project and module health: `mint doctor`, `mint healthcheck`, `mint next`, `mint status`."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from ..config import (
    MintConfig,
    load_config,
)
from ..errors import MintError
from ..gitutil import (
    git_available,
)
from ..renderer import (
    cassette_model,
    is_model_provider,
)
from ..renderer.templates import known_templates
from ..specs import Spec, parse_spec_file
from ..state import (
    load_metadata,
)
from ..stacks import adapter_for_stack
from .context import MAX_RESOURCE_CHARS, _changed, _prefix_lines, compute_context_hashes, configured_specs_dir, load_context, renderer_model, renderer_provider, resolve_required_order, selected_renderer_provider
from .lint import lint_module
from .planning import determine_render_plan


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

    if git_available():
        messages.append("git: available")
    else:
        failures.append(
            "git is not runnable; Mint checkpoints generated code with git "
            "(fix: install git and ensure it is on PATH)"
        )

    probe_env = os.environ.copy()
    probe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Probe the interpreter the project scripts will actually use (render exports
    # PYTHON_BIN=sys.executable unless the caller overrides it). The prepare script
    # requires `$PYTHON_BIN -m pip`, so a pip-less interpreter must fail doctor, not
    # the first render.
    python_bin = probe_env.get("PYTHON_BIN") or sys.executable
    for module_name, version_args, fix in [
        ("pip", ["-m", "pip", "--version"], f"{python_bin} -m ensurepip --upgrade"),
        ("pytest", ["-m", "pytest", "--version"], "pip install -e '.[dev]'"),
    ]:
        probe = subprocess.run(
            [python_bin, *version_args],
            cwd=root,
            env=probe_env,
            check=False,
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if probe.returncode == 0:
            messages.append(f"{module_name}: {probe.stdout.strip()}")
        else:
            failures.append(f"{module_name} is not runnable for {python_bin} (fix: {fix})")

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
            try:
                stack_health = adapter.healthcheck(load_context(spec.module, root))
            except MintError as exc:
                # One unloadable spec must not abort the whole doctor report.
                failures.append(f"{spec.module}: {exc}")
            else:
                messages.extend(
                    message
                    for message in stack_health.messages
                    if not message.startswith("Stack adapter:")
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
                continue
            try:
                text = resource_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                failures.append(
                    f"Linked resource for {unit.id} is not UTF-8 text: {resource} — "
                    "resources are embedded verbatim in render prompts, so only "
                    "text files are supported."
                )
                continue
            if len(text) > MAX_RESOURCE_CHARS:
                failures.append(
                    f"Linked resource for {unit.id} is too large to embed: {resource} "
                    f"({len(text)} chars, cap {MAX_RESOURCE_CHARS})."
                )

    if context.generated_dir.exists():
        try:
            metadata = load_metadata(context.generated_dir)
        except MintError as exc:
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
