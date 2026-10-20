"""Live progress lines for long-running commands.

``mint render`` can take minutes per unit on a live provider, and the command
output is a report built up and printed at the end. These lines go to stderr
*while* the render runs so the terminal is never silent: which unit, which
phase, which attempt, and how long since the command started.

Enabled when stderr is a TTY. ``MINT_PROGRESS=1`` forces it on (useful for CI
logs), ``MINT_PROGRESS=0`` forces it off. Progress never goes to stdout, so
``mint render mod > report.txt`` keeps the report clean either way.
"""

from __future__ import annotations

import os
import sys
import time

_started: float | None = None


def enabled() -> bool:
    flag = os.environ.get("MINT_PROGRESS", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def report(message: str) -> None:
    """Emit one progress line (no-op when progress is disabled)."""
    global _started
    if not enabled():
        return
    now = time.monotonic()
    if _started is None:
        _started = now
    elapsed = now - _started
    try:
        sys.stderr.write(f"mint [+{elapsed:6.1f}s] {message}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        # A closed or broken stderr must never take the render down with it.
        pass


def reset_clock() -> None:
    """Restart the elapsed counter (one command = one clock)."""
    global _started
    _started = None
