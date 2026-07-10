---
name: timesheet
description: Show today's and this week's logged hours from the timesheet store
disable-model-invocation: true
allowed-tools: Bash
---

## Logged time

This week's entries (mint-generated timesheet CLI; empty output means nothing
logged or the modules are not rendered yet):

!`"$CLAUDE_PROJECT_DIR/examples/timesheet/claude/timesheet.sh" list --week "$(date +%G-W%V)" 2>&1 || true`

Today is !`date +%F`.

Summarize the entries above for the user:

- **Today**: total hours and per-project hours for entries dated today.
- **This week**: total hours and per-project hours across all lines.

Each line is `#id project date hours status`. Sum the hours yourself and
present a short, readable summary. If there are no entries, say nothing was
logged this week. If the output is an error about missing generated modules,
tell the user to run `cd examples/timesheet && mint render timesheet-cli`.
