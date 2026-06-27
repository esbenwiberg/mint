#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

"$PYTHON_BIN" -m pip --version >/dev/null

if "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  echo "Python and pytest are available."
elif command -v pytest >/dev/null 2>&1; then
  pytest --version >/dev/null
  echo "Python is available; using pytest from PATH."
else
  echo "pytest is required for the first generated target stack." >&2
  exit 1
fi
