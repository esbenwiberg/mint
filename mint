#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

# The pip-installed console script runs under whatever interpreter it was
# installed into, but this checked-in launcher is invoked via `./mint`, so the
# `python3` on PATH may predate the >=3.12 requirement. Rather than hard-pin a
# single version in the shebang (which fails on machines that only ship a
# newer or differently-named python3.x), discover a suitable interpreter and
# re-exec under it. The env marker prevents an infinite re-exec loop.
if sys.version_info < (3, 12) and not os.environ.get("_MINT_REEXEC"):
    for candidate in ("python3.14", "python3.13", "python3.12"):
        found = shutil.which(candidate)
        if found:
            os.environ["_MINT_REEXEC"] = "1"
            os.execv(found, [found, str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(
        f"mint requires Python 3.12 or newer, but the available interpreter is "
        f"{sys.version_info.major}.{sys.version_info.minor} and no python3.12+ "
        f"was found on PATH."
    )

if sys.version_info < (3, 12):
    sys.exit(
        f"mint requires Python 3.12 or newer, but this is "
        f"{sys.version_info.major}.{sys.version_info.minor}."
    )

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from mint_cli.cli import main

raise SystemExit(main())
