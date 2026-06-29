from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import MintError

DEFAULT_SPECS_DIR = ".mint/specs"
DEFAULT_GENERATED_DIR = ".mint/generated"
DEFAULT_CONFORMANCE_DIR = "conformance"


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
    specs_dir: str
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
        scripts = _mapping(raw["scripts"], "scripts", path)
        renderer = _mapping(raw["renderer"], "renderer", path)
        limits = _mapping(raw["limits"], "limits", path)
        test_quality_raw = _mapping(raw.get("testQuality", {}), "testQuality", path)
        specs_dir = validate_project_dir(raw.get("specsDir", DEFAULT_SPECS_DIR), "specsDir", path)
        generated_dir = validate_project_output_dir(
            raw.get("generatedDir", DEFAULT_GENERATED_DIR), "generatedDir", path
        )
        conformance_dir = validate_project_output_dir(
            raw.get("conformanceDir", DEFAULT_CONFORMANCE_DIR), "conformanceDir", path
        )
        if len({specs_dir, generated_dir, conformance_dir}) != 3:
            raise MintError(
                f"Invalid project directories in {path}: specsDir, generatedDir, "
                "and conformanceDir must differ."
            )

        return MintConfig(
            path=path,
            version=_int(raw["version"], "version", path),
            default_stack=str(raw["defaultStack"]),
            specs_dir=specs_dir,
            generated_dir=generated_dir,
            conformance_dir=conformance_dir,
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
                unit_retries=_int(limits["unitRetries"], "limits.unitRetries", path),
                conformance_retries=_int(
                    limits["conformanceRetries"], "limits.conformanceRetries", path
                ),
                max_functional_units_per_render=_int(
                    limits["maxFunctionalUnitsPerRender"],
                    "limits.maxFunctionalUnitsPerRender",
                    path,
                ),
                max_model_response_chars=_int(
                    limits.get("maxModelResponseChars", 200000),
                    "limits.maxModelResponseChars",
                    path,
                ),
                max_render_attempts=_int(
                    limits.get("maxRenderAttempts", 0), "limits.maxRenderAttempts", path
                ),
                max_render_tokens_estimate=_int(
                    limits.get("maxRenderTokensEstimate", 0),
                    "limits.maxRenderTokensEstimate",
                    path,
                ),
            ),
            test_quality=TestQualityConfig(
                enabled=_bool(test_quality_raw.get("enabled", True), "testQuality.enabled", path),
                min_coverage_percent=_int(
                    test_quality_raw.get("minCoveragePercent", 60),
                    "testQuality.minCoveragePercent",
                    path,
                ),
                mutation_probe=_bool(
                    test_quality_raw.get("mutationProbe", True),
                    "testQuality.mutationProbe",
                    path,
                ),
                mutation_max_candidates=_int(
                    test_quality_raw.get("mutationMaxCandidates", 3),
                    "testQuality.mutationMaxCandidates",
                    path,
                ),
            ),
        )
    except KeyError as exc:
        raise MintError(f"Missing required config key in {path}: {exc.args[0]}") from exc


def _mapping(value: Any, field_name: str, config_path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MintError(f"Invalid {field_name} in {config_path}: expected a mapping.")
    return value


def _int(value: Any, field_name: str, config_path: Path) -> int:
    if isinstance(value, bool):
        raise MintError(f"Invalid {field_name} in {config_path}: expected an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MintError(f"Invalid {field_name} in {config_path}: expected an integer.") from exc


def _bool(value: Any, field_name: str, config_path: Path) -> bool:
    if not isinstance(value, bool):
        raise MintError(f"Invalid {field_name} in {config_path}: expected true or false.")
    return value


def validate_project_dir(value: Any, field_name: str, config_path: Path) -> str:
    text = str(value).strip()
    candidate = Path(text)
    if not text or candidate == Path(".") or candidate.is_absolute() or ".." in candidate.parts:
        raise MintError(
            f"Invalid {field_name} in {config_path}: use a project-relative directory "
            "without '.', '..', or an absolute path."
        )
    if ".git" in candidate.parts:
        raise MintError(
            f"Invalid {field_name} in {config_path}: project directories cannot be inside .git."
        )
    return candidate.as_posix()


def validate_project_output_dir(value: Any, field_name: str, config_path: Path) -> str:
    return validate_project_dir(value, field_name, config_path)


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
