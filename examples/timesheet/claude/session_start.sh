#!/usr/bin/env bash
# Claude Code SessionStart hook: start a timesheet timer for this session.
#
# Fail-soft by design: a broken timesheet must never break a session, so every
# path out of this script is exit 0. Identity comes from env vars only:
#   TIMESHEET_PERSON   required; without it the hook does nothing
#   TIMESHEET_PROJECT  optional; defaults to the repo directory name

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PERSON="${TIMESHEET_PERSON:-}"
if [ -z "$PERSON" ]; then
  exit 0
fi

PROJECT="${TIMESHEET_PROJECT:-}"
if [ -z "$PROJECT" ]; then
  PROJECT="$(basename "${CLAUDE_PROJECT_DIR:-$PWD}")"
fi

# Exit 4 (timer already running, e.g. a second concurrent session) and any
# missing-module failure are deliberately swallowed.
"$HERE/timesheet.sh" start "$PERSON" "$PROJECT" >/dev/null 2>&1 || true
exit 0
