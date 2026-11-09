#!/usr/bin/env python3
"""Kept for the ci scripts and READMEs that name it: runs ``sync.py --variant ci``.

``sync.py`` handles every variant (ci, full, lite); see its docstring.
"""

from __future__ import annotations

import sys

import sync

if __name__ == "__main__":
    sys.exit(sync.main([*sys.argv[1:], "--variant", "ci"]))
