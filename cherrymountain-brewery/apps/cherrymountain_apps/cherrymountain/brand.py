"""Where the brewery's brand goes on the hosts the app renders.

``brand/`` holds the mark and the colors; ``sync.py`` copies its files into
``phenix-injects/brand/`` and the renderers inject them from there, so a changed mark
reaches every host through the copy step and nothing here holds a second copy. This
module only says which file lands where on which kind of host. The colors of the
screens are not here: FUXA's come from ``fuxa/build_project.py``.
"""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

#: The name as it is written on every screen.
NAME: Final[str] = "CherryMountain Brewery"


class BrandFile(BaseModel):
    """One file of ``phenix-injects/brand/`` and where a host keeps it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    dst: str
    description: str


#: FUXA (``/opt/fuxa``, DESIGN.md 12.2): the client's tab icon replaces the one FUXA
#: ships (``client/dist/index.html`` links ``favicon.ico``), and the mark goes where FUXA
#: serves images from without a sign-in (``server/main.js`` ``/_images``), which is where
#: the project's ``navigation.logo`` and the sign-in dialog read it (``LOGO_URL`` in
#: fuxa/build_project.py).
FUXA_FILES: Final[tuple[BrandFile, ...]] = (
    BrandFile(
        name="favicon.ico",
        dst="/opt/fuxa/client/dist/favicon.ico",
        description="the brewery's tab icon for FUXA",
    ),
    BrandFile(
        name="icon.svg",
        dst="/opt/fuxa/server/_images/Brand/icon.svg",
        description="the brewery's mark for FUXA's menu and sign-in dialog",
    ),
)

#: The page title of each FUXA role (66-fuxa-brand.sh writes it into the client): the
#: role is in the title so the two instances can be told apart in the white cell's tabs.
FUXA_TITLES: Final[dict[str, str]] = {
    "scada": f"{NAME} SCADA",
    "bas": f"{NAME} BAS",
}
