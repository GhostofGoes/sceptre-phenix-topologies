#!/usr/bin/env python3
"""Run the ``cherrymountain`` app's and the ``plantevent`` component's tests
on this checkout.

Both live in sceptre-phenix-apps (``phenix_apps.apps.cherrymountain`` and
``phenix_apps.apps.scorch.plantevent``), and their tests ship with the
installed package. They read plant.yaml, the plantgen output, the FUXA builder,
the injects, and the program ZIPs (Git LFS) from a topology directory, which
``CHERRYMOUNTAIN_TOPOLOGY_DIR`` names. This sets it to the
``cherrymountain-brewery`` directory this script is in, so the tests check the
app against the files of this commit.

Extra arguments go to pytest, for example ``-k mirror``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: cherrymountain-brewery/
ROOT: Path = Path(__file__).resolve().parents[2]
PACKAGES: tuple[str, ...] = (
    "phenix_apps.apps.cherrymountain",
    "phenix_apps.apps.scorch.plantevent",
)


def main(argv: list[str]) -> int:
    os.environ["CHERRYMOUNTAIN_TOPOLOGY_DIR"] = str(ROOT)
    # Empty: human-readable logs on stderr, not phenix's log file.
    os.environ["PHENIX_LOG_FILE"] = ""
    return int(
        pytest.main(["-q", "-p", "no:cacheprovider", *argv, "--pyargs", *PACKAGES])
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
