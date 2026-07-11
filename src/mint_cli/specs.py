from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .config import parse_simple_yaml
from .errors import MintError
from .hashing import hash_json


UNIT_ID_RE = re.compile(r"^FR[0-9]+$")
MODULE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
# A class prefix must end in a separator so "ts-" cannot accidentally match "table".
STYLE_LOCK_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*-$")


@dataclass(frozen=True)
class Definition:
    name: str
    text: str


@dataclass(frozen=True)
class StyleLock:
    """Mechanical no-freelance-styling contract for a consumer module.

    When set, mint itself scans the module's generated sources on every render
    attempt: no ``<style`` element, no ``style=`` attribute, and every token in
    every ``class`` attribute must start with ``class_prefix``. ``kit`` names
    the required module that owns the look; it is validation sugar (must appear
    in ``requires``) and prompt context, not part of the scan.
    """

    class_prefix: str
    kit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"classPrefix": self.class_prefix}
        if self.kit:
            data["kit"] = self.kit
        return data


@dataclass(frozen=True)
class FunctionalUnit:
    id: str
    title: str
    spec: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "spec": self.spec,
            "acceptance": self.acceptance,
            "resources": self.resources,
        }


@dataclass(frozen=True)
class Spec:
    path: Path
    module: str
    description: str
    imports: list[str]
    requires: list[str]
    stack: str
    template: str | None
    renderer_provider: str | None
    renderer_model: str | None
    renderer_prompt_version: str | None
    definitions: list[Definition]
    implementation: list[str]
    test: list[str]
    functional_units: list[FunctionalUnit]
    style_lock: StyleLock | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {
            "module": self.module,
            "description": self.description,
            "imports": self.imports,
            "requires": self.requires,
            "stack": self.stack,
            "template": self.template,
            "renderer": {
                "provider": self.renderer_provider,
                "model": self.renderer_model,
                "promptVersion": self.renderer_prompt_version,
            },
            "definitions": [
                {"name": definition.name, "text": definition.text}
                for definition in self.definitions
            ],
            "implementation": self.implementation,
            "test": self.test,
            "functionalUnits": [unit.to_dict() for unit in self.functional_units],
        }
        # Only present when set: adding the key unconditionally would change every
        # existing spec's hash and invalidate every recorded cassette at once.
        if self.style_lock is not None:
            ir["styleLock"] = self.style_lock.to_dict()
        return ir

    def imported_context_ir(self) -> dict[str, Any]:
        """The slice of this spec another module pulls in via `imports`:
        shared definitions and implementation/test requirements."""
        return {
            "module": self.module,
            "definitions": [
                {"name": definition.name, "text": definition.text}
                for definition in self.definitions
            ],
            "implementation": self.implementation,
            "test": self.test,
        }

    def non_functional_ir(self) -> dict[str, Any]:
        ir = self.to_ir()
        ir["functionalUnits"] = []
        return ir

    @property
    def spec_hash(self) -> str:
        return hash_json(self.to_ir())

    @property
    def non_functional_hash(self) -> str:
        return hash_json(self.non_functional_ir())


def parse_spec_file(path: Path) -> Spec:
    if not path.exists():
        raise MintError(f"Spec file not found: {path}")

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    frontmatter, body = split_frontmatter(text, path)
    metadata = parse_simple_yaml(frontmatter)
    sections = parse_sections(body)

    definitions = parse_definitions(sections.get("definitions", []))
    implementation = parse_bullet_list(sections.get("implementation", []), "implementation")
    test = parse_bullet_list(sections.get("test", []), "test")
    functional_units = parse_functional_units(sections.get("functional", []))

    try:
        module = str(metadata["module"])
        description = str(metadata.get("description", ""))
        imports = ensure_string_list(metadata.get("imports", []), "imports")
        requires = ensure_string_list(metadata.get("requires", []), "requires")
        stack = str(metadata["stack"])
        template_raw = metadata.get("template")
        template = str(template_raw) if template_raw not in (None, "") else None
        provider_raw = metadata.get("rendererProvider")
        renderer_provider = str(provider_raw) if provider_raw not in (None, "") else None
        model_raw = metadata.get("rendererModel")
        renderer_model = str(model_raw) if model_raw not in (None, "") else None
        prompt_raw = metadata.get("rendererPromptVersion")
        renderer_prompt_version = str(prompt_raw) if prompt_raw not in (None, "") else None
    except KeyError as exc:
        raise MintError(f"Missing required spec frontmatter key in {path}: {exc.args[0]}") from exc

    if not MODULE_REF_RE.match(module):
        raise MintError(
            f"Invalid module '{module}' in {path}: use a lowercase module slug "
            "like taskstore or calc-cli."
        )
    if module in imports:
        raise MintError(f"Spec {path} cannot import itself ('{module}')")
    if module in requires:
        raise MintError(f"Spec {path} cannot require itself ('{module}')")
    validate_module_refs(path, imports, "imports")
    validate_module_refs(path, requires, "requires")
    style_lock = parse_style_lock(metadata.get("styleLock"), path, requires)

    # Specs must be named <module>.mint.md; anchoring on the full suffix keeps
    # the extension from leaking into the module name (e.g. file bar.md with
    # module: bar.md).
    if not path.name.endswith(".mint.md"):
        raise MintError(f"Spec file must be named <module>.mint.md: {path.name}")
    expected = path.name[: -len(".mint.md")]
    if expected != module:
        raise MintError(f"Spec module '{module}' does not match filename {path.name}")

    validate_spec_parts(path, definitions, implementation, test, functional_units)

    return Spec(
        path=path,
        module=module,
        description=description,
        imports=imports,
        requires=requires,
        stack=stack,
        template=template,
        renderer_provider=renderer_provider,
        renderer_model=renderer_model,
        renderer_prompt_version=renderer_prompt_version,
        definitions=definitions,
        implementation=implementation,
        test=test,
        functional_units=functional_units,
        style_lock=style_lock,
    )


def split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise MintError(f"Spec is missing YAML frontmatter: {path}")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise MintError(f"Spec frontmatter is not closed: {path}")


def parse_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in body.splitlines():
        if raw_line.startswith("## "):
            current = raw_line[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw_line)
    return sections


def parse_definitions(lines: list[str]) -> list[Definition]:
    definitions: list[Definition] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- ") or ":" not in stripped:
            raise MintError(f"Invalid definition line: {line}")
        name, text = stripped[2:].split(":", 1)
        name = name.strip()
        if not name:
            raise MintError(f"Definition is missing a name: {line}")
        definitions.append(Definition(name=name, text=text.strip()))
    return definitions


def parse_bullet_list(lines: list[str], section: str) -> list[str]:
    items: list[str] = []
    current: list[str] | None = None
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_fence and current is not None:
                current.append("")
            continue
        if stripped.startswith("- ") and not in_fence:
            if current is not None:
                items.append("\n".join(current).strip())
            current = [stripped[2:].strip()]
            continue
        if stripped.startswith("```"):
            if current is None:
                raise MintError(
                    f"Invalid {section} fenced code block: place code fences after a bullet."
                )
            current.append(stripped)
            in_fence = not in_fence
            continue
        if in_fence:
            if current is not None:
                current.append(line.rstrip())
            continue
        if current is not None and line[:1].isspace():
            current.append(line.rstrip())
            continue
        raise MintError(f"Invalid {section} bullet: {line}")
    if in_fence:
        raise MintError(f"Unclosed {section} fenced code block.")
    if current is not None:
        items.append("\n".join(current).strip())
    return items


def parse_functional_units(lines: list[str]) -> list[FunctionalUnit]:
    units: list[FunctionalUnit] = []
    current: dict[str, Any] | None = None
    current_list: str | None = None

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        units.append(
            FunctionalUnit(
                id=str(current.get("id", "")).strip(),
                title=str(current.get("title", "")).strip(),
                spec=list(current.get("spec", [])),
                acceptance=list(current.get("acceptance", [])),
                resources=list(current.get("resources", [])),
            )
        )
        current = None

    unit_indent = 0

    for line in lines:
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        # A `- id:` line only starts a new unit at the unit's own indent level.
        # Deeper `- id:` text (e.g. an acceptance bullet "id: must be unique")
        # is a list item, not a new unit — otherwise it hijacks parsing and the
        # error gets pinned on the wrong unit.
        if stripped.startswith("- id:") and (current is None or indent <= unit_indent):
            finish_current()
            unit_indent = indent
            current = {"id": stripped.split(":", 1)[1].strip(), "spec": [], "acceptance": [], "resources": []}
            current_list = None
            continue
        if current is None:
            raise MintError(f"Functional content appeared before an id: {line}")
        if stripped.startswith("title:"):
            current["title"] = stripped.split(":", 1)[1].strip()
            current_list = None
        elif stripped in {"spec:", "acceptance:", "resources:"}:
            current_list = stripped[:-1]
        elif stripped.startswith("- "):
            if current_list not in {"spec", "acceptance", "resources"}:
                raise MintError(f"Functional list item has no active list: {line}")
            current[current_list].append(stripped[2:].strip())
        else:
            raise MintError(f"Invalid functional line: {line}")

    finish_current()
    return units


def parse_style_lock(value: Any, path: Path, requires: list[str]) -> StyleLock | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise MintError(
            f"styleLock in {path} must be a mapping with classPrefix (and optional kit)."
        )
    unknown = sorted(set(value) - {"classPrefix", "kit"})
    if unknown:
        raise MintError(
            f"Unknown styleLock key(s) in {path}: {', '.join(unknown)}. "
            "Supported keys: classPrefix, kit."
        )
    prefix = str(value.get("classPrefix") or "")
    if not STYLE_LOCK_PREFIX_RE.match(prefix):
        raise MintError(
            f"styleLock.classPrefix in {path} must be letters/digits/dashes ending "
            f"in '-' (like 'ts-'); got {prefix!r}."
        )
    kit_raw = value.get("kit")
    kit = str(kit_raw) if kit_raw not in (None, "") else None
    if kit is not None and kit not in requires:
        raise MintError(
            f"styleLock.kit '{kit}' in {path} must be listed in requires — the kit "
            "module owns the look the style lock protects."
        )
    return StyleLock(class_prefix=prefix, kit=kit)


def ensure_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise MintError(f"Expected {field_name} to be a list")
    return [str(item) for item in value]


def validate_module_refs(path: Path, values: list[str], field_name: str) -> None:
    for value in values:
        if not MODULE_REF_RE.match(value):
            raise MintError(
                f"Invalid {field_name} module reference '{value}' in {path}: "
                "use a lowercase module slug like taskstore or calc-cli."
            )


def validate_spec_parts(
    path: Path,
    definitions: list[Definition],
    implementation: list[str],
    test: list[str],
    functional_units: list[FunctionalUnit],
) -> None:
    if not definitions:
        raise MintError(f"Spec has no definitions section: {path}")
    if not implementation:
        raise MintError(f"Spec has no implementation requirements: {path}")
    if not test:
        raise MintError(f"Spec has no test requirements: {path}")
    if not functional_units:
        raise MintError(f"Spec has no functional units: {path}")

    seen: set[str] = set()
    previous_number: int | None = None
    for unit in functional_units:
        if not UNIT_ID_RE.match(unit.id):
            raise MintError(
                f"Invalid functional unit id '{unit.id}' in {path}: "
                f"ids must look like FR1, FR2, ..."
            )
        if unit.id in seen:
            raise MintError(f"Duplicate functional unit id '{unit.id}' in {path}")
        seen.add(unit.id)

        number = int(unit.id[2:])
        if previous_number is not None and number <= previous_number:
            raise MintError(
                f"Functional units in {path} must be in strictly ascending order; "
                f"'{unit.id}' (FR{number}) follows FR{previous_number}. "
                f"Fix: reorder the ## functional section so ids increase."
            )
        previous_number = number

        if not unit.title:
            raise MintError(f"Functional unit {unit.id} in {path} is missing a title")
        if not unit.spec:
            raise MintError(f"Functional unit {unit.id} in {path} is missing spec bullets")
        if not unit.acceptance:
            raise MintError(f"Functional unit {unit.id} in {path} is missing acceptance bullets")
