"""cm-sensor is cm-malcolm with a shorter image list and a smaller disk.

The sensor of a site runs Malcolm in its ``hedgehog`` run profile, with the
scripts that the topology injects into ``malcolm``. Those scripts depend on the
paths, the packages, and the versions of cm-malcolm, so the two recipes must
not differ in anything but the image list, the disk size, and comments.
"""

from __future__ import annotations

import re

import yaml

from soc_paths import ROOT

IMAGES = ROOT / "phenix-images"

# The images of the services that have the profile `hedgehog` in the
# docker-compose.yml of Malcolm v26.10.0 (read on 2026-11-25).
HEDGEHOG_IMAGES = {
    "arkime",
    "filebeat-oss",
    "filescan",
    "pcap-capture",
    "pcap-monitor",
    "strelka-backend",
    "strelka-frontend",
    "strelka-manager",
    "suricata",
    "valkey",
    "zeek",
}

IMAGE_LOOP = re.compile(r"^\s*for name in (?P<names>[a-z0-9 -]+); do$", re.MULTILINE)


def spec(name: str) -> dict:
    return yaml.safe_load((IMAGES / f"{name}.yml").read_text())["spec"]


def install_script(name: str) -> str:
    return spec(name)["scripts"]["INSTALL_MALCOLM"]


def image_names(name: str) -> list[str]:
    loops = IMAGE_LOOP.findall(install_script(name))
    assert len(loops) == 1, f"{name}: expected one image loop, found {len(loops)}"
    return loops[0].split()


def code_lines(script: str) -> list[str]:
    """The lines of a script that are not comments, without the image loop."""
    return [
        line
        for line in script.splitlines()
        if not line.lstrip().startswith("#") and not IMAGE_LOOP.match(line)
    ]


def test_the_sensor_holds_the_images_of_the_hedgehog_profile() -> None:
    names = image_names("cm-sensor")
    assert len(names) == len(set(names))
    assert set(names) == HEDGEHOG_IMAGES


def test_each_sensor_image_is_also_in_malcolm() -> None:
    assert set(image_names("cm-sensor")) < set(image_names("cm-malcolm"))


def test_the_install_scripts_differ_only_in_the_image_list() -> None:
    assert code_lines(install_script("cm-sensor")) == code_lines(install_script("cm-malcolm"))


def test_the_recipes_differ_only_in_the_script_and_the_disk() -> None:
    sensor, malcolm = spec("cm-sensor"), spec("cm-malcolm")
    assert sensor.pop("size") == "32G"
    assert malcolm.pop("size") == "64G"
    for recipe in (sensor, malcolm):
        recipe["scripts"] = {key: value for key, value in recipe["scripts"].items() if key != "INSTALL_MALCOLM"}
    assert sensor == malcolm
