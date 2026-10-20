"""Style-lock enforcement: detecting and explaining style-lock violations in rendered files."""

from __future__ import annotations

from pathlib import Path
import re

from ..specs import FunctionalUnit, StyleLock


# --------------------------------------------------------------------------- #
# Style lock: mechanical no-freelance-styling enforcement for consumer modules
# --------------------------------------------------------------------------- #

_STYLE_TAG_RE = re.compile(r"<style\b", re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r"""(?<![\w-])style\s*=\s*["']""", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r"""(?<![\w-])class\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)


def style_lock_violations(src_dir: Path, lock: StyleLock) -> list[str]:
    """Scan a module's generated sources for style-lock violations.

    Source-level enforcement: the consumer's code must not *contain* freelance
    styling, so no recording can ship it. Only ``src/`` is scanned — tests and
    conformance legitimately quote forbidden substrings in their assertions.
    """
    violations: list[str] = []
    if not src_dir.exists():
        return violations
    kit = f"the {lock.kit} module" if lock.kit else "the design kit"
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = f"src/{path.relative_to(src_dir).as_posix()}"
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _STYLE_TAG_RE.search(line):
                violations.append(
                    f"{rel}:{lineno}: emits a <style element; only {kit} page shell owns styling"
                )
            if _STYLE_ATTR_RE.search(line):
                violations.append(f"{rel}:{lineno}: inline style= attribute is forbidden")
            for match in _CLASS_ATTR_RE.finditer(line):
                for token in match.group(2).split():
                    if not token.startswith(lock.class_prefix):
                        violations.append(
                            f"{rel}:{lineno}: class token '{token}' does not start "
                            f"with '{lock.class_prefix}'"
                        )
    return violations


def format_style_lock_failure(unit: FunctionalUnit, violations: list[str]) -> str:
    shown = violations[:20]
    lines = [f"Style lock violations in {unit.id}:", *(f"- {item}" for item in shown)]
    if len(violations) > len(shown):
        lines.append(f"- ... and {len(violations) - len(shown)} more")
    lines.append(
        "Remove every <style> element and style= attribute from module sources, and "
        "use only kit-prefixed class names in class attributes."
    )
    return "\n".join(lines)


def style_lock_prompt_hints(lock: StyleLock) -> list[str]:
    kit = f"the required {lock.kit} module" if lock.kit else "the design kit"
    return [
        "Style lock (mint scans src/ mechanically on every attempt): never emit a "
        "<style element or a style= attribute, and every token in every class "
        f"attribute must start with '{lock.class_prefix}'. All styling comes from {kit}."
    ]
