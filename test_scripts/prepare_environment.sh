#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

"$PYTHON_BIN" -m pip --version >/dev/null

if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
  exit 1
fi

echo "Python and pytest are available."
