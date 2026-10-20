from __future__ import annotations

from pathlib import Path

import pytest

from mint_cli.config import load_config, parse_scalar
from mint_cli.errors import MintError


CONFIG = """version: 1
defaultStack: python-cli
specsDir: {specs}
generatedDir: {generated}
conformanceDir: {conformance}
scripts:
  unit: test_scripts/run_unit_tests.sh
  conformance: test_scripts/run_conformance_tests.sh
  prepare: test_scripts/prepare_environment.sh
renderer:
  provider: local
  model: deterministic-v0
  promptVersion: v0
limits:
  unitRetries: 1
  conformanceRetries: 1
  maxFunctionalUnitsPerRender: 20
testQuality:
  enabled: true
"""


def write_config(
    tmp_path: Path,
    *,
    specs: str = ".mint/specs",
    generated: str = ".mint/generated",
    conformance: str = "conformance",
) -> Path:
    path = tmp_path / "mint.yaml"
    path.write_text(
        CONFIG.format(specs=specs, generated=generated, conformance=conformance),
        encoding="utf-8",
    )
    return path


def test_output_dirs_must_be_project_relative(tmp_path):
    with pytest.raises(MintError, match="generatedDir.*project-relative"):
        load_config(write_config(tmp_path, generated="/tmp/generated"))

    with pytest.raises(MintError, match="conformanceDir.*absolute"):
        load_config(write_config(tmp_path, conformance="../conformance"))


def test_output_dirs_must_be_distinct(tmp_path):
    with pytest.raises(MintError, match="must differ"):
        load_config(write_config(tmp_path, generated="generated", conformance="generated"))


def test_specs_dir_defaults_to_dot_mint_specs(tmp_path):
    path = write_config(tmp_path)
    text = path.read_text(encoding="utf-8").replace("specsDir: .mint/specs\n", "")
    path.write_text(text, encoding="utf-8")

    config = load_config(path)

    assert config.specs_dir == ".mint/specs"


def test_generated_dir_defaults_to_dot_mint_generated(tmp_path):
    path = write_config(tmp_path)
    text = path.read_text(encoding="utf-8").replace("generatedDir: .mint/generated\n", "")
    path.write_text(text, encoding="utf-8")

    config = load_config(path)

    assert config.generated_dir == ".mint/generated"


def test_specs_dir_must_be_project_relative(tmp_path):
    with pytest.raises(MintError, match="specsDir.*project-relative"):
        load_config(write_config(tmp_path, specs="/tmp/specs"))


def test_specs_dir_must_differ_from_output_dirs(tmp_path):
    with pytest.raises(MintError, match="specsDir, generatedDir, and conformanceDir must differ"):
        load_config(write_config(tmp_path, specs="generated", generated="generated"))


def test_load_config_accepts_safe_output_dirs(tmp_path):
    config = load_config(write_config(tmp_path, generated="generated", conformance="conformance"))

    assert config.specs_dir == ".mint/specs"
    assert config.generated_dir == "generated"
    assert config.conformance_dir == "conformance"


def test_invalid_numeric_config_values_raise_minterror(tmp_path):
    path = write_config(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("version: 1", "version: nope"))

    with pytest.raises(MintError, match="Invalid version.*expected an integer"):
        load_config(path)


def test_wrong_shaped_test_quality_config_raises_minterror(tmp_path):
    path = write_config(tmp_path)
    text = path.read_text(encoding="utf-8").replace(
        "testQuality:\n  enabled: true\n",
        "testQuality: false\n",
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(MintError, match="Invalid testQuality.*expected a mapping"):
        load_config(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", 1),
        ("-1", -1),
        ("+3", 3),
        ("007", 7),
        ("1.5", 1.5),
        ("-0.25", -0.25),
        ("1e3", "1e3"),
        ("true", True),
        ("null", None),
        ("~", None),
        ('"-1"', "-1"),
        ("'007'", "007"),
        ("sonnet", "sonnet"),
        ("claude-sonnet-4-5", "claude-sonnet-4-5"),
        ("[a, -1, 2.5]", ["a", -1, 2.5]),
    ],
)
def test_parse_scalar_numbers_and_literals(text, expected):
    assert parse_scalar(text) == expected


@pytest.mark.parametrize("bad", ["unitRetries: -1", "unitRetries: 1.5", "unitRetries: true"])
def test_limit_fields_reject_negative_float_and_bool(tmp_path, bad):
    path = write_config(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("unitRetries: 1", bad))

    with pytest.raises(MintError, match="Invalid limits.unitRetries"):
        load_config(path)
