#!/usr/bin/env python3
"""Run netcheck without installing it (the prek hook uses this)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netcheck.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
