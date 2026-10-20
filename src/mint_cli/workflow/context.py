"""Module context: loading a module's spec, config, metadata, and the payloads handed to renderers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_SPECS_DIR,
    MintConfig,
    load_config,
)
from ..errors import MintError
from ..hashing import hash_json
from ..modgraph import build_render_order
from ..specs import FunctionalUnit, Spec, parse_spec_file
from ..stacks import StackAdapter, adapter_for_stack


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
        # A render "attempt" is only consumed when the renderer actually produced
        # output. Non-render steps -- conformance run attempt 1 and the test-quality
        # phase -- pass prompt=response=None and must not burn the attempt budget
        # (that made maxRenderAttempts drain ~3x too fast). Tokens still accumulate
        # for whatever text a step carried.
        if prompt is not None or response is not None:
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
    # requiredModuleCodeHash covers exactly the context payload dependents' render
    # prompts embed (adapter.required_context_files — public-interface stubs for
    # Python). Internal-only upstream changes therefore neither alter this hash nor
    # the dependents' prompts: no cascade re-render, and their cassettes stay valid.
    required_entries = sorted(
        (
            {
                "module": module,
                "codeHash": hash_json(_required_context_entry(context, module)["files"]),
            }
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


def selected_renderer_provider(spec: Spec, config: MintConfig) -> str:
    return (spec.renderer_provider or config.renderer.provider).strip().lower()


def _prefix_lines(text: str, prefix: str) -> list[str]:
    return [prefix + line for line in text.rstrip().splitlines()]


def metadata_path_for_message(context: ModuleContext) -> str:
    return (context.generated_dir / ".mintgen" / "module.json").relative_to(context.root).as_posix()


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _changed(stored: Any, current: Any) -> str:
    return "changed" if stored != current else "unchanged"


def _module_interface_files(context: ModuleContext) -> list[dict[str, str]]:
    if not context.generated_dir.exists():
        return []
    return context.stack_adapter.required_context_files(context.generated_dir)


def _dependent_modules(context: ModuleContext) -> list[str]:
    """Modules whose transitive requires include this module (they cascade on
    interface changes)."""
    loader = _spec_loader(context.root, context.config.specs_dir)
    dependents: list[str] = []
    for spec_path in sorted((context.root / context.config.specs_dir).glob("*.mint.md")):
        try:
            spec = parse_spec_file(spec_path)
            if spec.module == context.module:
                continue
            order = build_render_order(spec.module, loader)
        except MintError:
            continue
        if context.module in (m for m in order if m != spec.module):
            dependents.append(spec.module)
    return dependents


def _required_context_entry(context: ModuleContext, module: str) -> dict[str, Any]:
    """The per-required-module context payload: what dependents' prompts embed AND
    what requiredModuleCodeHash hashes. One source of truth so the cascade fires
    exactly when the visible context changes."""
    spec = parse_spec_file(context.spec_path(module))
    adapter = adapter_for_stack(spec.stack)
    return {
        "module": module,
        "files": adapter.required_context_files(context.generated_dir_for(module)),
    }


def _required_modules_payload(
    context: ModuleContext, required_order: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [_required_context_entry(context, module) for module in required_order]


# Resource files are embedded verbatim in the unit's render prompt; the cap keeps
# one oversized asset from blowing the prompt (and the cassette) out of shape.
MAX_RESOURCE_CHARS = 24000


def _unit_resources_payload(context: ModuleContext, unit: FunctionalUnit) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for rel in unit.resources:
        path = context.root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise MintError(
                f"Linked resource missing for {unit.id}: {rel}. Fix: restore the file "
                f"or remove it from the unit's resources (see mint healthcheck {context.module})."
            ) from exc
        except (UnicodeDecodeError, OSError) as exc:
            raise MintError(
                f"Linked resource for {unit.id} is not UTF-8 text: {rel} — resources "
                "are embedded verbatim in render prompts, so only text files are supported."
            ) from exc
        if len(text) > MAX_RESOURCE_CHARS:
            raise MintError(
                f"Linked resource for {unit.id} is too large to embed: {rel} "
                f"({len(text)} chars, cap {MAX_RESOURCE_CHARS})."
            )
        payload.append({"path": rel, "contents": text})
    return payload


def _module_files_payload(context: ModuleContext) -> list[dict[str, Any]]:
    """The module's own generated files as they exist right now (full source, not
    stubs — the model must extend this code, not just call it). Computed per render
    request so later units and retry attempts see the current tree."""
    adapter = context.stack_adapter
    files: list[dict[str, Any]] = []
    total = 0
    for path in adapter.required_payload_files(context.generated_dir):
        contents = path.read_text(encoding="utf-8")
        total += len(contents)
        files.append(
            {
                "path": path.relative_to(context.generated_dir).as_posix(),
                "contents": contents,
                "language": adapter.code_fence_language,
            }
        )
    cap = context.config.limits.max_module_files_chars
    if total > cap:
        largest = sorted(files, key=lambda f: len(f["contents"]), reverse=True)[:3]
        listing = ", ".join(f"{f['path']} ({len(f['contents'])} chars)" for f in largest)
        raise MintError(
            f"Module {context.module} is too large to embed in a render prompt: its "
            f"generated sources total {total} chars (limits.maxModuleFilesChars is {cap}). "
            f"Largest: {listing}. Fix: split the module — move a cohesive slice into its "
            f"own spec and reference it through `requires:` (dependents only see its "
            f"public interface stub, not its full source). Raising the cap in mint.yaml "
            f"is the escape hatch, not the fix."
        )
    return files


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
