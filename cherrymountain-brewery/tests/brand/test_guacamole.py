"""The Guacamole branding extension: the jar sync.py builds, the compose file's pin and mount, and
the topology's injection. Guacamole was not run; its CSS was rendered on the markup of the
1.6.0 templates (phenix-images/README.md)."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml
from support import INJECTS, PREFIX, ROOT, topology_nodes

COMPOSE: dict[str, Any] = yaml.safe_load((INJECTS / "guacamole" / "docker-compose.yml").read_text())
JAR: Path = INJECTS / "guacamole" / "cherrymountain-brand.jar"


def test_the_extension_is_for_the_guacamole_version_the_compose_file_pins() -> None:
    image: str = COMPOSE["services"]["guacamole"]["image"]
    version = image.split(":")[1]
    with zipfile.ZipFile(JAR) as zf:
        manifest = json.loads(zf.read("guac-manifest.json"))
    assert manifest["guacamoleVersion"] == version == "1.6.0"
    assert manifest["namespace"] == "cherrymountain-brand"


def test_the_extension_holds_the_brand_files_and_the_name() -> None:
    brand = ROOT / "brand"
    with zipfile.ZipFile(JAR) as zf:
        manifest = json.loads(zf.read("guac-manifest.json"))
        assert zf.read(manifest["smallIcon"]) == (brand / "icon-64.png").read_bytes()
        assert zf.read(manifest["largeIcon"]) == (brand / "icon-256.png").read_bytes()
        assert zf.read("icon.svg") == (brand / "icon.svg").read_bytes()
        assert zf.read("tokens.css") == (INJECTS / "brand" / "tokens.css").read_bytes()
        assert json.loads(zf.read("translations/en.json"))["APP"]["NAME"] == "CherryMountain Brewery"
        assert zf.read(manifest["smallIcon"])[:8] == b"\x89PNG\r\n\x1a\n"  # Guacamole serves it as image/png
        for key in ("css", "translations"):
            assert all(n in zf.namelist() for n in manifest[key])


def test_the_extension_css_names_no_color_and_fetches_nothing() -> None:
    with zipfile.ZipFile(JAR) as zf:
        css = zf.read("brand.css").decode()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css), "brand.css takes every color from tokens.css"
    assert not re.search(r"https?:|//", css.replace("/*", "").replace("*/", ""))
    for url in re.findall(r"url\(\"?([^)\"]+)", css):
        assert url.startswith("app/ext/cherrymountain-brand/"), url


def test_the_compose_file_mounts_the_extension_where_guacamole_reads_extensions() -> None:
    volumes: list[str] = COMPOSE["services"]["guacamole"]["volumes"]
    assert (
        "/opt/cherrymountain/guacamole/cherrymountain-brand.jar:"
        "/etc/guacamole/extensions/cherrymountain-brand.jar:ro"
    ) in volumes


def test_the_remote_access_topology_injects_the_extension_to_the_mounted_path() -> None:
    node = topology_nodes()["dmz-jump"]
    injects = {i["dst"]: i for i in node["injections"]}
    jar = injects["/opt/cherrymountain/guacamole/cherrymountain-brand.jar"]
    assert jar["src"] == PREFIX + "guacamole/cherrymountain-brand.jar" and jar["permissions"] == "0644"
