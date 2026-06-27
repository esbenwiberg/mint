#!/usr/bin/env python3.12
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from mint_cli.cli import main

raise SystemExit(main())
