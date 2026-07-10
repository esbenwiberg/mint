#!/usr/bin/env bash
# Claude Code SessionStart hook: start a timesheet timer for this session.
#
# Fail-soft by design: a broken timesheet must never break a session, so every
# path out of this script is exit 0. The timesheet is a personal, single-user
# app — no identity env var is needed:
#   TIMESHEET_PROJECT  optional; defaults to the repo directory name

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT="${TIMESHEET_PROJECT:-}"
if [ -z "$PROJECT" ]; then
  PROJECT="$(basename "${CLAUDE_PROJECT_DIR:-$PWD}")"
fi

# Exit 4 (timer already running, e.g. a second concurrent session) and any
# missing-module failure are deliberately swallowed.
"$HERE/timesheet.sh" start "$PROJECT" >/dev/null 2>&1 || true
exit 0
