"""First-boot provisioning of the SOC against facts of the pinned upstream releases
(DESIGN.md 14).

No VM, Malcolm, or Wazuh runs here. Each test pins one thing that the upstream
source decides and that broke, or would break, the first boot:

* Malcolm 26.10.0 (``scripts/control.py``, ``docker-compose.yml``, ``config/*.env.example``),
* Wazuh 4.14 (``wazuh-passwords-tool.sh``, the agent package's ``ossec.conf``),
* Filebeat 7.10.2 (the keystore lives in ``path.data``, ``/var/lib/filebeat``),
* systemd (a ``ReadWritePaths=`` entry that does not exist fails the unit).
"""

from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from soc_paths import INJECTS, ROOT

MALCOLM: Path = INJECTS / "malcolm"
WAZUH: Path = INJECTS / "wazuh"
IMAGES: Path = ROOT / "phenix-images"
AGENT_SCRIPT: Path = ROOT / "apps/cherrymountain_apps/cherrymountain/templates/scripts/80-cm-wazuh-agent.sh"

#: The env file of Malcolm 26.10.0 that defines each variable cm-malcolm-provision
#: sets (config/<file>.env.example). Compose reads a service's env files in order and
#: the last one wins, so a variable set in any other file is overridden by the blank
#: or default value here. A variable Malcolm does not define is absent.
MALCOLM_ENV_HOME: dict[str, str] = {
    "PUID": "process",
    "PGID": "process",
    "PCAP_PIPELINE_VERBOSITY": "upload-common",
    "PCAP_NODE_NAME": "upload-common",
    "ZEEK_LOCAL_NETS": "zeek",
    "PCAP_IFACE": "pcap-capture",
    "PCAP_IFACE_TWEAK": "pcap-capture",
    "ZEEK_LIVE_CAPTURE": "zeek-live",
    "ARKIME_LIVE_CAPTURE": "arkime-live",
    "SURICATA_LIVE_CAPTURE": "suricata-live",
    "SURICATA_UPDATE_RULES": "suricata",
    "SURICATA_UPDATE_SOURCES": "suricata",
    "OPENSEARCH_INDEX_SIZE_PRUNE_LIMIT": "dashboards-helper",
    "MANAGE_PCAP_FILES": "arkime",
    "ARKIME_FREESPACEG": "arkime",
    "NGINX_AUTH_MODE": "auth-common",
    "NETBOX_MODE": "netbox-common",
    "NETBOX_ENRICHMENT": "netbox-common",
    "NETBOX_AUTO_POPULATE": "netbox-common",
    "NETBOX_DEFAULT_SITE": "netbox-common",
    "PIPELINE_DISABLED": "pipeline",
}
#: Bind-mount sources of Malcolm 26.10.0's compose file that are not in its checkout
#: (create_host_path: false, so a missing one fails `compose up`), by service.
MALCOLM_BIND_SOURCES: dict[str, str] = {"valkey": "valkey", "postgres": "postgres", "netbox": "netbox/media"}

#: The <client> block of the wazuh-agent 4.14.10 package's ossec.conf, and the line of
#: its inventory settings that a grep for <groups> finds.
STOCK_AGENT_CONF: str = """<ossec_config>
  <client>
    <server>
      <address>MANAGER_IP</address>
      <port>1514</port>
      <protocol>tcp</protocol>
    </server>
    <config-profile>debian, debian7</config-profile>
    <notify_time>20</notify_time>
    <time-reconnect>60</time-reconnect>
    <auto_restart>yes</auto_restart>
    <crypto_method>aes</crypto_method>
  </client>

  <wodle name="syscollector">
    <disabled>no</disabled>
    <users>yes</users>
    <groups>yes</groups>
  </wodle>
</ossec_config>
"""


def provision() -> str:
    return (MALCOLM / "cm-malcolm-provision").read_text()


def test_malcolm_variables_go_to_the_env_file_that_defines_them() -> None:
    calls = re.findall(r"^\s*set_env config/([\w-]+)\.env (\w+) ", provision(), re.MULTILINE)
    assert calls, "no set_env calls found"
    wrong = {key: file for file, key in calls if MALCOLM_ENV_HOME.get(key, file) != file}
    assert not wrong, f"set in a file that a later env file overrides: {wrong}"
    # ZEEK_LOCAL_NETS is the one that was in the wrong file.
    assert ("zeek", "ZEEK_LOCAL_NETS") in calls


def test_malcolm_scripts_run_with_the_docker_group() -> None:
    """scripts/control.py runs `docker info` before auth_setup and raises when it fails."""
    body = re.search(r"^as_malcolm\(\) \{\n(.*?)^\}", provision(), re.MULTILINE | re.DOTALL)
    assert body is not None
    assert "--clear-groups" not in body.group(1)
    assert '--groups="${DOCKER_GROUP}"' in body.group(1)
    # A root shell's login name would make the scripts refuse to run.
    assert "USER=malcolm" in body.group(1) and "LOGNAME=malcolm" in body.group(1)


def test_malcolm_bind_mount_directories_are_created() -> None:
    mkdir = re.search(r"^mkdir -p opensearch/nodes(?:.*\\\n)*.*$", provision(), re.MULTILINE)
    assert mkdir is not None
    made = set(mkdir.group(0).replace("\\\n", " ").split()[2:])
    profiles = (MALCOLM / "profiles.sh").read_text()
    for service, source in MALCOLM_BIND_SOURCES.items():
        assert re.search(rf"\b{service}\b", profiles), f"{service} is not started by any profile"
        assert source in made, f"./{source} ({service}) is not created before compose up"


def test_malcolm_start_retries_until_provisioned(tmp_path: Path) -> None:
    unit = (MALCOLM / "cm-malcolm.service").read_text()
    assert not re.search(r"^ConditionPathExists=", unit, re.MULTILINE)
    assert re.search(r"^Restart=on-failure$", unit, re.MULTILINE)
    proc = subprocess.run(
        ["bash", str(MALCOLM / "cm-malcolm-start")],
        env={**os.environ, "CM_MALCOLM_STATE": str(tmp_path), "CM_MALCOLM_SRC": str(MALCOLM)},
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert proc.returncode == 1
    assert "not provisioned yet" in proc.stdout


def unit_setting(unit: str, key: str) -> list[str]:
    return [word for line in re.findall(rf"^{key}=(.*)$", unit, re.MULTILINE) for word in line.split()]


@pytest.mark.parametrize("unit_path", sorted(INJECTS.glob("*/*.service")), ids=lambda p: p.name)
def test_writable_paths_exist_before_the_unit_starts(unit_path: Path) -> None:
    """systemd fails a unit (226/NAMESPACE) whose ReadWritePaths= entry is missing,
    before any script can create it. Each entry must come from a package, be
    optional (a leading -), or be created by an image script."""
    packaged = ("/etc", "/var/log", "/var/ossec", "/usr/share/wazuh-", "/var/lib/wazuh-indexer")
    images = "\n".join(path.read_text() for path in IMAGES.glob("cm-*.yml"))
    for entry in unit_setting(unit_path.read_text(), "ReadWritePaths"):
        if entry.startswith("-") or (entry.startswith(packaged) and entry != "/etc/cherrymountain"):
            continue
        assert re.search(rf"(install -d|mkdir -p|git clone)[^\n]* {re.escape(entry)}(/|\s|$)", images), (
            f"{unit_path.name}: no image script creates {entry}"
        )


def test_wazuh_state_directories_are_made_by_systemd() -> None:
    unit = (WAZUH / "cm-wazuh-provision.service").read_text()
    state = unit_setting(unit, "StateDirectory")
    # The markers and passwords.txt, and Filebeat's path.data (filebeat.keystore).
    assert {"cm-wazuh", "filebeat"} <= set(state)
    assert "/var/lib/cm-wazuh" not in unit_setting(unit, "ReadWritePaths")


def test_wazuh_password_tool_runs_last_and_with_the_api_flag() -> None:
    script = (WAZUH / "cm-wazuh-provision").read_text()
    tool = re.search(r'^.*wazuh-passwords-tool\.sh" (.*?) > ', script, re.MULTILINE)
    assert tool is not None
    flags = tool.group(1).split()
    # wazuh-passwords-tool.sh: -au and -ap without -A print the help and exit 1.
    assert {"-A", "-a", "-au", "-ap"} <= set(flags)
    # The tool restarts the dashboard and edits its wazuh.yml, so the dashboard is up first.
    dashboard = script.index("systemctl enable --now wazuh-dashboard.service")
    assert dashboard < tool.start()
    assert script.index("wazuh-dashboard.service", tool.end()) > tool.end(), "the dashboard is not restarted after the tool"


def run_agent_script(tmp_path: Path, group: str) -> str:
    (tmp_path / "etc").mkdir(exist_ok=True)
    conf = tmp_path / "etc" / "ossec.conf"
    if not conf.exists():
        conf.write_text(STOCK_AGENT_CONF)
    env_file = tmp_path / "agent.env"
    env_file.write_text(
        f"WAZUH_MANAGER=10.40.10.11\nWAZUH_ENROLLMENT_PORT=1515\nWAZUH_AGENT_GROUP={group}\nWAZUH_REGISTRATION_PASSWORD=secret\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("chgrp", "systemctl"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / name).chmod(0o755)
    subprocess.run(
        ["bash", str(AGENT_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CM_WAZUH_AGENT_ENV": str(env_file),
            "CM_OSSEC_DIR": str(tmp_path),
        },
        check=True, capture_output=True, text=True, timeout=30,
    )
    return conf.read_text()


def test_the_agent_enrolls_into_its_zone_group(tmp_path: Path) -> None:
    """The packaged ossec.conf has no <enrollment> block and a <groups>yes</groups>
    of its own; the agent must still enroll into its group, or it never gets the
    group's agent.conf (the dmz group's log files feed rules 100105 to 100314)."""
    text = run_agent_script(tmp_path, "dmz")
    root = ET.fromstring(text)
    client = root.find("client")
    assert client is not None
    assert client.findtext("server/address") == "10.40.10.11"
    assert client.findtext("enrollment/groups") == "dmz"
    assert client.findtext("enrollment/port") == "1515"
    assert (tmp_path / "etc" / "authd.pass").read_text() == "secret\n"
    # A second boot changes nothing.
    assert run_agent_script(tmp_path, "dmz") == text
    assert text.count("<enrollment>") == 1
