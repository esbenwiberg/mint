from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import MintError


@dataclass(frozen=True)
class ScriptConfig:
    unit: str
    conformance: str
    prepare: str


@dataclass(frozen=True)
class RendererConfig:
    provider: str
    model: str
    prompt_version: str


@dataclass(frozen=True)
class LimitConfig:
    unit_retries: int
    conformance_retries: int
    max_functional_units_per_render: int
    max_model_response_chars: int
    max_render_attempts: int
    max_render_tokens_estimate: int


@dataclass(frozen=True)
class TestQualityConfig:
    enabled: bool
    min_coverage_percent: int
    mutation_probe: bool
    mutation_max_candidates: int


@dataclass(frozen=True)
class MintConfig:
    path: Path
    version: int
    default_stack: str
    generated_dir: str
    conformance_dir: str
    scripts: ScriptConfig
    renderer: RendererConfig
    limits: LimitConfig
    test_quality: TestQualityConfig

    @property
    def root(self) -> Path:
        return self.path.parent


def load_config(path: Path) -> MintConfig:
    if not path.exists():
        raise MintError(f"Config file not found: {path}")

    raw = parse_simple_yaml(path.read_text(encoding="utf-8"))
    try:
        scripts = raw["scripts"]
        renderer = raw["renderer"]
        limits = raw["limits"]
        test_quality_raw = raw.get("testQuality", {})
        return MintConfig(
            path=path,
            version=int(raw["version"]),
            default_stack=str(raw["defaultStack"]),
            generated_dir=str(raw["generatedDir"]),
            conformance_dir=str(raw["conformanceDir"]),
            scripts=ScriptConfig(
                unit=str(scripts["unit"]),
                conformance=str(scripts["conformance"]),
                prepare=str(scripts["prepare"]),
            ),
            renderer=RendererConfig(
                provider=str(renderer["provider"]),
                model=str(renderer["model"]),
                prompt_version=str(renderer["promptVersion"]),
            ),
            limits=LimitConfig(
                unit_retries=int(limits["unitRetries"]),
                conformance_retries=int(limits["conformanceRetries"]),
                max_functional_units_per_render=int(limits["maxFunctionalUnitsPerRender"]),
                max_model_response_chars=int(limits.get("maxModelResponseChars", 200000)),
                max_render_attempts=int(limits.get("maxRenderAttempts", 0)),
                max_render_tokens_estimate=int(limits.get("maxRenderTokensEstimate", 0)),
            ),
            test_quality=TestQualityConfig(
                enabled=bool(test_quality_raw.get("enabled", True)),
                min_coverage_percent=int(test_quality_raw.get("minCoveragePercent", 60)),
                mutation_probe=bool(test_quality_raw.get("mutationProbe", True)),
                mutation_max_candidates=int(test_quality_raw.get("mutationMaxCandidates", 3)),
            ),
        )
    except KeyError as exc:
        raise MintError(f"Missing required config key in {path}: {exc.args[0]}") from exc


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped:
            raise MintError(f"Invalid YAML-like line {line_no}: {raw_line}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise MintError(f"Invalid indentation on line {line_no}: {raw_line}")

        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_scalar(value)

    return root


def parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in inner.split(",")]
    if value in {"true", "false"}:
        return value == "true"
    if value.isdigit():
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value
