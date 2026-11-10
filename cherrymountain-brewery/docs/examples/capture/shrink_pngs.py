"""Save PNGs with a 256-color palette, to keep the set of pictures small.

    python shrink_pngs.py FILE.png ...

FUXA's and the kiosk's screens are flat colors and gray text, so a 256-color
palette (no dithering) looks the same and takes about 40 % of the bytes. The
size and every pixel's place are unchanged. The cm-viz pictures are 3D scenes
and stay in full color.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


def main(argv: list[str]) -> int:
    """Re-save each file in place."""
    for name in argv[1:]:
        path = Path(name)
        with Image.open(path) as im:
            im.convert("RGB").quantize(
                colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
            ).save(path, optimize=True)
        print(f"{path.name}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
