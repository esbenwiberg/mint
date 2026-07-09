#!/usr/bin/env bash
# Wrapper around the mint-generated timesheet CLI.
#
# Hand-written glue, not mint-generated: it wires PYTHONPATH to the generated
# modules' src dirs and defaults the store/timer env vars, then execs the CLI.
#
#   TIMESHEET_STORE   entry store  (default: ~/.timesheet/store.json)
#   TIMESHEET_TIMER   timer file   (default: ~/.timesheet/timer.json)
#   TIMESHEET_PYTHON  interpreter  (default: python3.12, then python3)
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERATED="$HERE/../.mint/generated"

for module in timesheet-cli timestore rules; do
  if [ ! -d "$GENERATED/$module/src" ]; then
    echo "timesheet: generated module missing: $module" >&2
    echo "timesheet: render it first: cd examples/timesheet && mint render timesheet-cli" >&2
    exit 1
  fi
done

PYTHON_BIN="${TIMESHEET_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3.12 || command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "timesheet: no python3 interpreter found" >&2
  exit 1
fi

export TIMESHEET_STORE="${TIMESHEET_STORE:-$HOME/.timesheet/store.json}"
export TIMESHEET_TIMER="${TIMESHEET_TIMER:-$HOME/.timesheet/timer.json}"
mkdir -p "$(dirname "$TIMESHEET_STORE")" "$(dirname "$TIMESHEET_TIMER")"

export PYTHONPATH="$GENERATED/timesheet-cli/src:$GENERATED/timestore/src:$GENERATED/rules/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON_BIN" -m timesheet_cli.cli "$@"
