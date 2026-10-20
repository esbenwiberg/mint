"""Render planning: which unit range a render touches and why (metadata drift, explicit ranges)."""

from __future__ import annotations

from typing import Any

from ..errors import MintError
from ..state import (
    record_by_unit,
    unit_text_hash,
)
from .context import ContextHashes, ModuleContext, RenderPlan, parse_unit_range, unit_index


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
        start_index = unit_index(context.spec, start_unit)
        _guard_explicit_range(context, metadata, hashes, start_index, "--range")
        return RenderPlan(
            start_index,
            unit_index(context.spec, end_unit),
            "explicit range",
        )
    if from_unit:
        start_index = unit_index(context.spec, from_unit)
        _guard_explicit_range(context, metadata, hashes, start_index, "--from")
        return RenderPlan(start_index, end_index, "explicit --from")
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
        if record.get("textHash") != unit_text_hash(unit, context.root):
            return RenderPlan(index, end_index, f"functional unit changed: {unit.id}")
        if record.get("status") != "passed":
            return RenderPlan(index, end_index, f"incomplete functional unit: {unit.id}")

    if len(records) != len(context.spec.functional_units):
        return RenderPlan(0, end_index, "functional unit set changed")
    return RenderPlan(0, end_index, "already rendered", noop=True)


def _guard_explicit_range(
    context: ModuleContext,
    metadata: dict[str, Any] | None,
    hashes: ContextHashes,
    start_index: int,
    flag: str,
) -> None:
    """Refuse partial renders that would silently mask drift or start mid-air.

    An explicit ``--from``/``--range`` bypasses the incremental drift checks. Two
    ways that goes wrong:

    * ``start_index > 0`` with no metadata wipes the module and renders only the
      tail, leaving earlier units ungenerated while exiting 0.
    * ``start_index > 0`` while stored context hashes have drifted would render the
      tail against new inputs, keep the head against old inputs, and then
      ``refresh_metadata_hashes`` would stamp the current hashes -- permanently
      masking the drift (the next plain render reports NOOP).
    """
    if start_index == 0:
        return
    if metadata is None:
        start_unit = context.spec.functional_units[start_index].id
        raise MintError(
            f"Cannot render {context.module} {flag} {start_unit}: no checkpoint recorded "
            f"(there is no prior render metadata). "
            f"Fix: run a full render first (`mint render {context.module}`) before "
            "targeting a later unit."
        )

    drift = _explicit_range_drift(context, metadata, hashes)
    if drift:
        start_unit = context.spec.functional_units[start_index].id
        details = "; ".join(drift)
        raise MintError(
            f"Refusing partial render of {context.module} {flag} {start_unit}: "
            f"context inputs changed since the last render ({details}). "
            "A partial render would leave earlier units built against stale inputs and "
            "then stamp the current hashes, permanently masking the drift. "
            f"Fix: run a full render (`mint render {context.module} --force`) to rebuild "
            "every unit against the new inputs."
        )


def _explicit_range_drift(
    context: ModuleContext,
    metadata: dict[str, Any],
    hashes: ContextHashes,
) -> list[str]:
    drift: list[str] = []
    if metadata.get("nonFunctionalSpecHash") != context.spec.non_functional_hash:
        drift.append("non-functional spec changed")
    if metadata.get("importedContextHash") != hashes.imported_context_hash:
        drift.append("imported context changed")
    if metadata.get("requiredModuleCodeHash") != hashes.required_module_code_hash:
        drift.append("required module code changed")
    return drift
