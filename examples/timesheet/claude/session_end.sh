#!/usr/bin/env bash
# Claude Code SessionEnd hook: stop the running timesheet timer so the
# session's duration lands in the store as a draft entry.
#
# Fail-soft by design: every path out of this script is exit 0. With no
# running timer (hook never started one, or another session already stopped
# it) the CLI exits 4 and that is deliberately swallowed.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${TIMESHEET_PERSON:-}" ]; then
  exit 0
fi

"$HERE/timesheet.sh" stop >/dev/null 2>&1 || true
exit 0
