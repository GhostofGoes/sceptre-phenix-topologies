#!/usr/bin/env python3
"""Compare two program ZIPs by entry names and contents (not raw bytes: deflate
output can differ between zlib builds while the archive holds the same files).

Exit 0 when they match, 1 (listing the entries that differ) when they do not,
and 3 when the committed file is missing or not a ZIP (for example a Git LFS
pointer: run ``git lfs pull`` rather than rebuilding over it).

Usage: python zipdiff.py FRESH.zip COMMITTED.zip
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path


def entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def short(data: bytes | None) -> str:
    return hashlib.md5(data).hexdigest()[:12] if data is not None else "-"  # noqa: S324


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    fresh_path, committed_path = Path(argv[0]), Path(argv[1])
    fresh: dict[str, bytes] = entries(fresh_path)
    if not committed_path.is_file():
        print(f"zipdiff: {committed_path} does not exist", file=sys.stderr)
        return 3
    try:
        committed: dict[str, bytes] = entries(committed_path)
    except zipfile.BadZipFile:
        head: bytes = committed_path.read_bytes()[:64]
        kind: str = (
            "a Git LFS pointer; run git lfs pull"
            if head.startswith(b"version https://git-lfs")
            else "not a ZIP"
        )
        print(f"zipdiff: {committed_path} is {kind}", file=sys.stderr)
        return 3
    if fresh == committed:
        return 0
    for name in sorted(set(fresh) | set(committed)):
        a, b = fresh.get(name), committed.get(name)
        if a != b:
            print(f"zipdiff:   {name}: fresh {short(a)} committed {short(b)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
