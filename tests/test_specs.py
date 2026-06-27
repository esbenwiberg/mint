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
