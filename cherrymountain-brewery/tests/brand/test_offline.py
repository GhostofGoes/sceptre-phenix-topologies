"""No screen of the brand fetches a font, icon, or stylesheet from outside: the images are
built offline-capable."""

from __future__ import annotations

import re

from support import INJECTS, ROOT


def test_no_page_of_the_brand_fetches_a_font_icon_or_stylesheet_from_outside() -> None:
    web = ROOT / "viz" / "cherrymountain_viz" / "web"
    files = [web / n for n in ("index.html", "svg.html", "style.css", "svg.css")]
    files += [INJECTS / "guacamole" / "branding" / "brand.css"]
    for path in files:
        text = path.read_text()
        assert not re.search(r"(?:src|href)=\"(?:https?:)?//", text), path.name
        assert not re.search(r"url\(\s*[\"']?(?:https?:)?//", text), path.name
        assert "@import" not in text, path.name
