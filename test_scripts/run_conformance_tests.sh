#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
MODULE="${1:-example}"
GENERATED_DIR="${MINT_GENERATED_DIR:-generated/$MODULE}"
CONFORMANCE_DIR="${MINT_CONFORMANCE_DIR:-conformance/$MODULE}"

if [ ! -d "$GENERATED_DIR" ]; then
  echo "Generated module directory not found: $GENERATED_DIR" >&2
  exit 1
fi

if [ ! -d "$CONFORMANCE_DIR" ]; then
  echo "Conformance test directory not found: $CONFORMANCE_DIR" >&2
  exit 1
fi

case "$GENERATED_DIR" in
  /*) GENERATED_SRC="$GENERATED_DIR/src" ;;
  *) GENERATED_SRC="$PWD/$GENERATED_DIR/src" ;;
esac

export PYTHONPATH="$GENERATED_SRC:${MINT_REQUIRED_SRC:-}:${PYTHONPATH:-}"

if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  echo "pytest is required for $PYTHON_BIN. Install with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
  exit 1
fi

"$PYTHON_BIN" -m pytest "$CONFORMANCE_DIR"
