"""Local regenerative coding CLI."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path


def _read_version() -> str:
    """Resolve the package version from a single source of truth.

    When installed (editable or otherwise) the version comes from the
    distribution metadata, which setuptools populates from the top-level
    ``VERSION`` file. When running straight from a source checkout via the
    ``./mint`` launcher the package is not installed, so we fall back to reading
    ``VERSION`` directly.
    """
    try:
        return metadata.version("mint-regenerative")
    except metadata.PackageNotFoundError:
        version_file = Path(__file__).resolve().parents[2] / "VERSION"
        return version_file.read_text(encoding="utf-8").strip()


__version__ = _read_version()
