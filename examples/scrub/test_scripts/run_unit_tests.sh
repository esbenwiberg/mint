#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
MODULE="${1:-example}"
GENERATED_DIR="${MINT_GENERATED_DIR:-.mint/generated/$MODULE}"

if [ ! -d "$GENERATED_DIR" ]; then
  echo "Generated module directory not found: $GENERATED_DIR" >&2
  exit 1
fi

cd "$GENERATED_DIR"
export PYTHONPATH="$PWD/src:${MINT_REQUIRED_SRC:-}:${PYTHONPATH:-}"

if [ "${MINT_SKIP_PYTEST_VERSION_CHECK:-0}" != "1" ]; then
  if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
    echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

"$PYTHON_BIN" -m pytest
