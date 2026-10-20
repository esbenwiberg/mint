"""Run reports and attempt manifests: `mint report` and `mint inspect`."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from ..state import (
    load_metadata,
    record_by_unit,
)
from .context import ModuleContext, _estimate_tokens, find_unit, load_context, metadata_path_for_message


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
        "- Cost estimate: not configured (no provider price table)",
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


def _attempt_manifest_paths(attempts_dir: Path) -> list[Path]:
    """Attempt manifests only, never sibling artifacts.

    ``write_attempt`` writes ``<phase>-<attempt>.json`` (the manifest) alongside
    ``<phase>-<attempt>.patch.json`` and other artifacts. A bare ``glob("*.json")``
    also matches the ``.patch.json`` files, which double-counts attempts and prints
    garbage rows. Manifest stems never contain a dot; every other json artifact does
    (``<base>.patch.json``), so filter on that.
    """
    manifests: list[Path] = []
    for path in attempts_dir.glob("*.json"):
        if not path.is_file():
            continue
        stem = path.name[: -len(".json")]
        if "." in stem:
            continue
        manifests.append(path)
    return sorted(manifests)


def _attempt_records(context: ModuleContext, unit_id: str) -> list[dict[str, Any]]:
    attempts_dir = context.generated_dir / ".mintgen" / "attempts" / unit_id
    if not attempts_dir.exists():
        return []

    attempts: list[dict[str, Any]] = []
    for path in _attempt_manifest_paths(attempts_dir):
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
    base = context.generated_dir.resolve()
    path = (context.generated_dir / relative).resolve()
    # Containment: the path comes from a JSON manifest that could be crafted to
    # escape the generated dir (../../etc/passwd). Refuse anything outside it.
    if path != base and base not in path.parents:
        return ""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


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
        for path in _attempt_manifest_paths(attempts_dir):
            data = json.loads(path.read_text(encoding="utf-8"))
            lines.append(
                f"- {path.name}: {data.get('phase')} attempt={data.get('attempt')} "
                f"renderer={data.get('renderer')} exit={data.get('exitCode')} "
                f"[{data.get('classification')}] {data.get('summary')}"
            )
    else:
        lines.append("Attempts: none")
    return 0, "\n".join(lines) + "\n"
