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
        _ensure_dirs_disjoint(specs_dir, generated_dir, conformance_dir, path)

        provider = _str(renderer["provider"], "renderer.provider", path)
        _validate_renderer_provider(provider, path)

        return MintConfig(
            path=path,
            version=_int(raw["version"], "version", path),
            default_stack=_str(raw["defaultStack"], "defaultStack", path),
            specs_dir=specs_dir,
            generated_dir=generated_dir,
            conformance_dir=conformance_dir,
            scripts=ScriptConfig(
                unit=_str(scripts["unit"], "scripts.unit", path),
                conformance=_str(scripts["conformance"], "scripts.conformance", path),
                prepare=_str(scripts["prepare"], "scripts.prepare", path),
            ),
            renderer=RendererConfig(
                provider=provider,
                model=_str(renderer["model"], "renderer.model", path),
                prompt_version=_str(renderer["promptVersion"], "renderer.promptVersion", path),
            ),
            limits=LimitConfig(
                unit_retries=_int_in_range(
                    limits["unitRetries"], "limits.unitRetries", path, minimum=0
                ),
                conformance_retries=_int_in_range(
                    limits["conformanceRetries"], "limits.conformanceRetries", path, minimum=0
                ),
                max_functional_units_per_render=_int_in_range(
                    limits["maxFunctionalUnitsPerRender"],
                    "limits.maxFunctionalUnitsPerRender",
                    path,
                    minimum=1,
                ),
                max_model_response_chars=_int_in_range(
                    limits.get("maxModelResponseChars", 200000),
                    "limits.maxModelResponseChars",
                    path,
                    minimum=1,
                ),
                max_render_attempts=_int_in_range(
                    limits.get("maxRenderAttempts", 0),
                    "limits.maxRenderAttempts",
                    path,
                    minimum=0,
                ),
                max_render_tokens_estimate=_int_in_range(
                    limits.get("maxRenderTokensEstimate", 0),
                    "limits.maxRenderTokensEstimate",
                    path,
                    minimum=0,
                ),
            ),
            test_quality=TestQualityConfig(
                enabled=_bool(test_quality_raw.get("enabled", True), "testQuality.enabled", path),
                min_coverage_percent=_int_in_range(
                    test_quality_raw.get("minCoveragePercent", 60),
                    "testQuality.minCoveragePercent",
                    path,
                    minimum=0,
                    maximum=100,
                ),
                mutation_probe=_bool(
                    test_quality_raw.get("mutationProbe", True),
                    "testQuality.mutationProbe",
                    path,
                ),
                mutation_max_candidates=_int_in_range(
                    test_quality_raw.get("mutationMaxCandidates", 3),
                    "testQuality.mutationMaxCandidates",
                    path,
                    minimum=0,
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


def _int_in_range(
    value: Any,
    field_name: str,
    config_path: Path,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    number = _int(value, field_name, config_path)
    if minimum is not None and number < minimum:
        raise MintError(
            f"Invalid {field_name} in {config_path}: must be >= {minimum}, got {number}."
        )
    if maximum is not None and number > maximum:
        raise MintError(
            f"Invalid {field_name} in {config_path}: must be <= {maximum}, got {number}."
        )
    return number


def _str(value: Any, field_name: str, config_path: Path) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        raise MintError(
            f"Invalid {field_name} in {config_path}: expected a non-empty text value."
        )
    text = str(value).strip()
    if not text:
        raise MintError(
            f"Invalid {field_name} in {config_path}: expected a non-empty text value."
        )
    return text


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


def _ensure_dirs_disjoint(
    specs_dir: str, generated_dir: str, conformance_dir: str, config_path: Path
) -> None:
    """specsDir, generatedDir, and conformanceDir must be fully disjoint.

    An exact-string check is not enough: nesting (e.g. generatedDir ``.mint``
    with specsDir ``.mint/specs``) lets a module render over / ``mint clean``
    delete another configured directory. Reject any dir that is a path prefix
    of another."""
    named = (
        ("specsDir", specs_dir),
        ("generatedDir", generated_dir),
        ("conformanceDir", conformance_dir),
    )
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            (name_a, dir_a), (name_b, dir_b) = named[i], named[j]
            parts_a, parts_b = Path(dir_a).parts, Path(dir_b).parts
            shared = min(len(parts_a), len(parts_b))
            if parts_a[:shared] == parts_b[:shared]:
                raise MintError(
                    f"Invalid project directories in {config_path}: specsDir, generatedDir, "
                    f"and conformanceDir must differ and cannot nest inside one another "
                    f"({name_a} '{dir_a}' overlaps {name_b} '{dir_b}')."
                )


def _validate_renderer_provider(provider: str, config_path: Path) -> None:
    # Local import keeps config.py free of a renderer import cycle.
    from .renderer import VALID_RENDERER_PROVIDERS

    if provider not in VALID_RENDERER_PROVIDERS:
        allowed = ", ".join(sorted(VALID_RENDERER_PROVIDERS))
        raise MintError(
            f"Invalid renderer.provider '{provider}' in {config_path}: use one of {allowed}."
        )


@dataclass
class _YamlFrame:
    # Column of the key that opened this mapping (-1 for the root document).
    key_col: int
    # Column shared by every key inside this mapping; None until the first
    # child is seen (so a freshly opened mapping can still decide its level).
    child_col: int | None
    mapping: dict[str, Any]


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[_YamlFrame] = [_YamlFrame(key_col=-1, child_col=0, mapping=root)]

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        leading = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        if "\t" in leading:
            raise MintError(
                f"Invalid indentation on line {line_no}: use spaces, not tabs, for YAML "
                f"indentation: {raw_line!r}"
            )
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped:
            raise MintError(f"Invalid YAML-like line {line_no}: {raw_line}")

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        value = _strip_inline_comment(raw_value.strip())

        # Close any dedented or empty (childless) mappings before placing the key.
        while len(stack) > 1:
            top = stack[-1]
            if top.child_col is None:
                if indent > top.key_col:
                    top.child_col = indent
                    break
                stack.pop()  # opened on the previous line but has no children
                continue
            if indent < top.child_col:
                stack.pop()
                continue
            break

        top = stack[-1]
        if top.child_col is None:
            top.child_col = indent
        if indent != top.child_col:
            raise MintError(
                f"Invalid indentation on line {line_no}: expected {top.child_col} "
                f"space(s), found {indent}: {raw_line!r}"
            )
        if key in top.mapping:
            raise MintError(
                f"Duplicate key '{key}' on line {line_no} in YAML-like input: {raw_line}"
            )

        if value == "":
            child: dict[str, Any] = {}
            top.mapping[key] = child
            stack.append(_YamlFrame(key_col=indent, child_col=None, mapping=child))
        else:
            top.mapping[key] = parse_scalar(value)

    return root


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ``# ...`` comment that sits outside quotes.

    YAML only treats ``#`` as a comment when it follows whitespace (or starts
    the token), so ``http://x#y`` and ``"a # b"`` stay intact."""
    in_single = in_double = False
    for i, ch in enumerate(value):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or value[i - 1] in (" ", "\t"):
                return value[:i].rstrip()
    return value


def _split_list_items(inner: str) -> list[str]:
    """Split an inline-list body on commas that sit outside quotes."""
    items: list[str] = []
    current: list[str] = []
    in_single = in_double = False
    for ch in inner:
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        if ch == "," and not in_single and not in_double:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return items


def parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in _split_list_items(inner)]
    if value in {"true", "false"}:
        return value == "true"
    if value.isdigit():
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value
