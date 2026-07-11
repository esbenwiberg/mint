from __future__ import annotations

from pathlib import Path

import pytest

from mint_cli.errors import MintError
from mint_cli.specs import parse_spec_file


GOOD = """---
module: widget
description: A widget
imports: []
requires: []
stack: python-lib
---

## definitions

- Widget: a thing.

## implementation

- Use Python 3.12.

## test

- Conformance tests use pytest.

## functional

- id: FR1
  title: First
  spec:
    - Does a thing.
  acceptance:
    - The thing happened.

- id: FR2
  title: Second
  spec:
    - Does another thing.
  acceptance:
    - The other thing happened.
"""


def write(tmp_path: Path, text: str, name: str = "widget") -> Path:
    path = tmp_path / f"{name}.mint.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_good_spec(tmp_path):
    spec = parse_spec_file(write(tmp_path, GOOD))
    assert spec.module == "widget"
    assert [u.id for u in spec.functional_units] == ["FR1", "FR2"]
    assert spec.imports == [] and spec.requires == []


def test_body_bullets_allow_fenced_code_blocks(tmp_path):
    text = GOOD.replace(
        "- Conformance tests use pytest.",
        "- Pin the exact test harness:\n"
        "```ts\n"
        "export default { test: { include: ['conformance/**/*.test.ts'] } };\n"
        "```\n"
        "- Conformance tests use pytest.",
    )

    spec = parse_spec_file(write(tmp_path, text))

    assert "```ts" in spec.test[0]
    assert "conformance/**/*.test.ts" in spec.test[0]
    assert spec.test[1] == "Conformance tests use pytest."


def test_missing_frontmatter(tmp_path):
    text = GOOD.split("---\n", 2)[2]  # drop the frontmatter block
    with pytest.raises(MintError, match="missing YAML frontmatter"):
        parse_spec_file(write(tmp_path, text))


def test_unclosed_frontmatter(tmp_path):
    text = "---\nmodule: widget\nstack: python-lib\n" + GOOD.split("---\n", 2)[2]
    with pytest.raises(MintError, match="frontmatter is not closed"):
        parse_spec_file(write(tmp_path, text))


def test_missing_required_key(tmp_path):
    text = GOOD.replace("stack: python-lib\n", "")
    with pytest.raises(MintError, match="Missing required spec frontmatter key"):
        parse_spec_file(write(tmp_path, text))


def test_missing_section(tmp_path):
    text = GOOD.replace("## definitions\n\n- Widget: a thing.\n\n", "")
    with pytest.raises(MintError, match="no definitions section"):
        parse_spec_file(write(tmp_path, text))


def test_duplicate_unit_ids(tmp_path):
    text = GOOD.replace("- id: FR2", "- id: FR1")
    with pytest.raises(MintError, match="Duplicate functional unit id"):
        parse_spec_file(write(tmp_path, text))


def test_bad_unit_order(tmp_path):
    # Swap so FR2 comes before FR1.
    text = GOOD.replace(
        "- id: FR1\n  title: First",
        "- id: FR9\n  title: First",
    ).replace(
        "- id: FR2\n  title: Second",
        "- id: FR3\n  title: Second",
    )
    with pytest.raises(MintError, match="ascending order"):
        parse_spec_file(write(tmp_path, text))


def test_invalid_unit_id(tmp_path):
    text = GOOD.replace("- id: FR1", "- id: XX1")
    with pytest.raises(MintError, match="Invalid functional unit id"):
        parse_spec_file(write(tmp_path, text))


def test_filename_must_match_module(tmp_path):
    with pytest.raises(MintError, match="does not match filename"):
        parse_spec_file(write(tmp_path, GOOD, name="other"))


def test_self_require_rejected(tmp_path):
    text = GOOD.replace("requires: []", "requires: [widget]")
    with pytest.raises(MintError, match="cannot require itself"):
        parse_spec_file(write(tmp_path, text))


def test_imports_and_requires_parsed(tmp_path):
    text = GOOD.replace("imports: []", "imports: [base]").replace(
        "requires: []", "requires: [base]"
    )
    spec = parse_spec_file(write(tmp_path, text))
    assert spec.imports == ["base"]
    assert spec.requires == ["base"]


@pytest.mark.parametrize("field", ["imports", "requires"])
@pytest.mark.parametrize("value", ["../base", "base/child", ".", ".."])
def test_dependency_names_must_be_module_slugs(tmp_path, field, value):
    text = GOOD.replace(f"{field}: []", f"{field}: [{value}]")
    with pytest.raises(MintError, match=f"Invalid {field} module reference"):
        parse_spec_file(write(tmp_path, text))


def test_template_key_optional(tmp_path):
    spec = parse_spec_file(write(tmp_path, GOOD))
    assert spec.template is None
    text = GOOD.replace("stack: python-lib", "stack: python-lib\ntemplate: widget-lib")
    spec2 = parse_spec_file(write(tmp_path, text))
    assert spec2.template == "widget-lib"


def test_unit_text_hash_changes_with_spec_text(tmp_path):
    from mint_cli.state import unit_text_hash

    spec = parse_spec_file(write(tmp_path, GOOD))
    changed = GOOD.replace("Does a thing.", "Does a different thing.")
    spec2 = parse_spec_file(write(tmp_path, changed))
    assert unit_text_hash(spec.functional_units[0]) != unit_text_hash(spec2.functional_units[0])
    # FR2 unchanged.
    assert unit_text_hash(spec.functional_units[1]) == unit_text_hash(spec2.functional_units[1])


def _with_style_lock(kit_line: str = "", prefix: str = "ts-") -> str:
    frontmatter = f"stack: python-lib\nstyleLock:\n  classPrefix: {prefix}"
    if kit_line:
        frontmatter += f"\n  kit: {kit_line}"
    return GOOD.replace("stack: python-lib", frontmatter)


def test_style_lock_parses_and_lands_in_ir(tmp_path):
    text = _with_style_lock("base").replace("requires: []", "requires: [base]")
    spec = parse_spec_file(write(tmp_path, text))
    assert spec.style_lock is not None
    assert spec.style_lock.class_prefix == "ts-"
    assert spec.style_lock.kit == "base"
    assert spec.to_ir()["styleLock"] == {"classPrefix": "ts-", "kit": "base"}


def test_style_lock_absent_keeps_ir_and_hash_stable(tmp_path):
    # The key must not exist at all when unset — adding it unconditionally would
    # change every existing spec hash and invalidate every recorded cassette.
    spec = parse_spec_file(write(tmp_path, GOOD))
    assert spec.style_lock is None
    assert "styleLock" not in spec.to_ir()


def test_style_lock_kit_must_be_required(tmp_path):
    with pytest.raises(MintError, match="must be listed in requires"):
        parse_spec_file(write(tmp_path, _with_style_lock("base")))


def test_style_lock_kit_is_optional(tmp_path):
    spec = parse_spec_file(write(tmp_path, _with_style_lock()))
    assert spec.style_lock is not None and spec.style_lock.kit is None
    assert spec.to_ir()["styleLock"] == {"classPrefix": "ts-"}


@pytest.mark.parametrize("prefix", ["", "ts", "-ts-", "ts_"])
def test_style_lock_prefix_must_end_in_dash(tmp_path, prefix):
    with pytest.raises(MintError, match="classPrefix"):
        parse_spec_file(write(tmp_path, _with_style_lock(prefix=prefix)))


def test_style_lock_rejects_unknown_keys(tmp_path):
    text = GOOD.replace(
        "stack: python-lib", "stack: python-lib\nstyleLock:\n  classPrefix: ts-\n  colour: red"
    )
    with pytest.raises(MintError, match="Unknown styleLock key"):
        parse_spec_file(write(tmp_path, text))


def test_unit_text_hash_requires_root_for_resource_units(tmp_path):
    from mint_cli.errors import MintError as StateMintError
    from mint_cli.state import unit_text_hash

    text = GOOD.replace(
        "  acceptance:\n    - The thing happened.",
        "  acceptance:\n    - The thing happened.\n  resources:\n    - resources/notes.txt",
        1,
    )
    spec = parse_spec_file(write(tmp_path, text))
    unit = spec.functional_units[0]
    assert unit.resources == ["resources/notes.txt"]

    with pytest.raises(StateMintError, match="requires the project root"):
        unit_text_hash(unit)

    resource = tmp_path / "resources" / "notes.txt"
    resource.parent.mkdir(parents=True)
    resource.write_text("v1", encoding="utf-8")
    first = unit_text_hash(unit, tmp_path)
    resource.write_text("v2", encoding="utf-8")
    assert unit_text_hash(unit, tmp_path) != first
    # Resource-less units hash identically with or without a root (back-compat).
    other = spec.functional_units[1]
    assert unit_text_hash(other) == unit_text_hash(other, tmp_path)
