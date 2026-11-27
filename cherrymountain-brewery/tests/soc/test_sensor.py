"""The first boot of a site's network sensor, and what `malcolm` does for the sensors
(docs/multisite/PLANS.md 4.6).

No VM and no Malcolm runs here. The scripts were written from the source of Malcolm
26.10.0, so each test pins a fact of that source, or runs a script against stand-ins
of the programs that it calls:

* ``scripts/installer/core/dependencies.py``: what the configurator derives from
  ``remoteMalcolmHost``, and which ports ``openPortsSelection: customize`` opens,
* ``scripts/control.py``: what ``auth_setup --auth-noninteractive`` does in the
  ``hedgehog`` run profile, and when it makes its own certificates,
* ``docker-compose.yml`` and ``config/*.env.example``: the services of the profile and
  the env file that defines each variable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from soc_paths import CONFIGS, INJECTS
from test_provisioning import MALCOLM_SERVICE_IMAGE, MALCOLM_SETTINGS
from test_sensor_image import image_names

SENSOR: Path = INJECTS / "sensor"
MALCOLM: Path = INJECTS / "malcolm"

#: The env file of Malcolm 26.10.0 that defines each variable cm-sensor-provision
#: sets (config/<file>.env.example). A variable set in another file is overridden by
#: the value here, because compose reads this file later. ZEEK_LB_PROCS_WORKER_DEFAULT
#: and PYTHONUNBUFFERED are in no example.
SENSOR_ENV_HOME: dict[str, str] = {
    "LOGSTASH_HOST": "beats-common",
    "BEATS_SSL": "beats-common",
    "OPENSEARCH_PRIMARY": "opensearch",
    "OPENSEARCH_URL": "opensearch",
    "OPENSEARCH_SSL_CERTIFICATE_VERIFICATION": "opensearch",
    "PCAP_NODE_NAME": "upload-common",
    "ARKIME_LIVE_CAPTURE": "arkime-live",
    "ARKIME_LIVE_NODE_HOST": "arkime-live",
    "ARKIME_WISE_SERVICE_URL": "arkime-secret",
    "PCAP_IFACE": "pcap-capture",
    "ZEEK_LOCAL_NETS": "zeek",
    "SURICATA_UPDATE_SOURCES": "suricata",
}
#: The env files that Malcolm's configurator writes and cm-sensor-provision edits.
CONFIGURED: tuple[str, ...] = (
    "process", "opensearch", "beats-common", "upload-common", "arkime-live", "pcap-capture", "zeek", "suricata",
)
#: The three conduits of a sensor (PLANS.md 4.6).
LOGSTASH_PORT, OPENSEARCH_PORT, VIEWER_PORT = 5044, 9200, 8005

PASSWORD = "Sensor0Password1"
SECRET = "ArkimeSecret0123456789"

#: `setpriv --reuid=... env ...` without the change of user, and a chown that does nothing.
STUBS: dict[str, str] = {
    "setpriv": '#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in --*) shift ;; *) break ;; esac; done\nexec "$@"\n',
    "chown": "#!/bin/sh\nexit 0\n",
}
#: Malcolm's configurator: keeps the settings file it was given and writes the env files.
CONFIGURE_STUB = """#!/bin/bash
set -eu
while [ $# -gt 0 ]; do
  [ "$1" = --import-malcolm-config-file ] && cp "$2" configured.yaml
  shift
done
mkdir -p config
printf 'MALCOLM_PROFILE=%s\\nPUID=1000\\n' "${FAKE_PROFILE:-hedgehog}" > config/process.env
printf 'OPENSEARCH_PRIMARY=opensearch-local\\nOPENSEARCH_URL=https://opensearch:9200\\n' > config/opensearch.env
printf 'LOGSTASH_HOST=logstash:5044\\nBEATS_SSL=false' > config/beats-common.env
printf 'PCAP_NODE_NAME=malcolm\\n' > config/upload-common.env
printf 'ARKIME_LIVE_CAPTURE=false\\n' > config/arkime-live.env
printf 'PCAP_IFACE=lo\\n' > config/pcap-capture.env
printf 'ZEEK_LOCAL_NETS=\\n' > config/zeek.env
printf 'SURICATA_UPDATE_SOURCES=true\\n' > config/suricata.env
"""
#: Malcolm's auth_setup: keeps its arguments and writes Arkime's secrets file.
AUTH_SETUP_STUB = """#!/bin/bash
set -eu
printf '%s\\n' "$@" > auth_setup.args
printf 'ARKIME_PASSWORD_SECRET=set\\nARKIME_WISE_SERVICE_URL=https://10.40.10.10/wise/\\n' > config/arkime-secret.env
"""


def script(name: str) -> str:
    return (SENSOR / name).read_text(encoding="utf-8")


def sensor_settings() -> dict[str, object]:
    data = yaml.safe_load(script("malcolm-config-sensor.yaml"))
    assert set(data) == {"configuration"}
    return data["configuration"]


def services(name: str) -> list[str]:
    (line,) = re.findall(r'^SERVICES="([a-z -]+)"$', script(name), re.MULTILINE)
    return line.split()


# --- the settings and the services ------------------------------------------------------


def test_the_sensors_settings_are_ones_the_configurator_knows() -> None:
    settings = sensor_settings()
    assert not set(settings) - MALCOLM_SETTINGS, "the configurator ignores an unknown key without a word"
    assert settings["malcolmProfile"] == "hedgehog"
    # The three words that cm-sensor-provision fills in, and no other.
    words = set(re.findall(r"@CM_[A-Z_]+@", script("malcolm-config-sensor.yaml")))
    assert words == {"@CM_SENSOR_NAME@", "@CM_SENSOR_ADDRESS@", "@CM_MALCOLM_HOST@"}
    assert (settings["pcapNodeName"], settings["liveArkimeNodeHost"], settings["remoteMalcolmHost"]) == (
        "@CM_SENSOR_NAME@", "@CM_SENSOR_ADDRESS@", "@CM_MALCOLM_HOST@",
    )
    for word in words:
        assert f"s/{word}/" in script("cm-sensor-provision")


def test_the_sensor_captures_with_arkime_and_sends_everything_away() -> None:
    """On `malcolm`, Arkime's own capture cannot run (test_provisioning: it waits for a
    local OpenSearch on the host network). On a sensor it is the one way: the sensor has
    no OpenSearch, and arkime-live writes its sessions to Malcolm's and serves the pcap."""
    settings = sensor_settings()
    assert settings["opensearchPrimaryMode"] == "opensearch-remote"
    assert settings["captureLiveNetworkTraffic"] is True and settings["pcapIface"] == ["phenix"]
    assert (settings["liveArkime"], settings["pcapTcpDump"], settings["pcapNetSniff"]) == (True, False, False)
    assert settings["liveZeek"] is True and settings["liveSuricata"] is True
    # No file scanning: the scanners' results would need a fourth conduit (8006/tcp).
    assert settings["fileCarveMode"] == "none" and settings["pipelineEnabled"] is False
    # The range has no registry of rules to update from.
    assert settings["suricataRuleUpdate"] is False
    assert settings["malcolmRestartPolicy"] == "unless-stopped"


def test_start_and_health_name_the_same_services() -> None:
    started = services("cm-sensor-start")
    assert started == services("cm-sensor-health")
    assert len(set(started)) == len(started)
    settings = sensor_settings()
    assert ("arkime-live" in started) == settings["liveArkime"]
    assert ("zeek-live" in started) == settings["liveZeek"]
    assert ("suricata-live" in started) == settings["liveSuricata"]
    # The capture container writes pcap files for the other Arkime, which a sensor does not run.
    assert "pcap-capture" not in started and "arkime" not in started
    assert "filebeat" in started
    start = script("cm-sensor-start")
    assert 'docker compose --profile hedgehog up --detach --no-deps "${services[@]}"' in start
    assert "docker compose --profile hedgehog stop" in script("cm-sensor-stop")


def test_the_image_holds_every_service_that_the_sensor_starts() -> None:
    """The range has no registry to pull from."""
    in_image = set(image_names("cm-sensor"))
    needed = {MALCOLM_SERVICE_IMAGE[service] for service in services("cm-sensor-start")}
    assert needed <= in_image, f"missing {needed - in_image}"


def test_sensor_variables_go_to_the_env_file_that_defines_them() -> None:
    calls = re.findall(r"^\s*set_env config/([\w-]+)\.env (\w+) ", script("cm-sensor-provision"), re.MULTILINE)
    assert calls, "no set_env calls found"
    wrong = {key: file for file, key in calls if SENSOR_ENV_HOME.get(key, file) != file}
    assert not wrong, f"set in a file that a later env file overrides: {wrong}"
    assert set(SENSOR_ENV_HOME) <= {key for _, key in calls}
    # A file that the configurator writes is checked before it is edited.
    edited = {file for file, key in calls if key in SENSOR_ENV_HOME} - {"arkime-secret"}
    assert edited <= set(CONFIGURED)
    for file in CONFIGURED:
        assert f"config/{file}.env" in script("cm-sensor-provision")


# --- cm-sensor-provision against stand-ins ----------------------------------------------


def checkout(tmp_path: Path) -> dict[str, str]:
    """A stand-in of the image: a Malcolm checkout, the injected files, and the path."""
    malcolm = tmp_path / "malcolm"
    (malcolm / "scripts").mkdir(parents=True)
    for name, body in (("configure", CONFIGURE_STUB), ("auth_setup", AUTH_SETUP_STUB)):
        (malcolm / "scripts" / name).write_text(body, encoding="utf-8")
        (malcolm / "scripts" / name).chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    source = tmp_path / "src"
    source.mkdir()
    shutil.copy(SENSOR / "malcolm-config-sensor.yaml", source)
    shutil.copy(MALCOLM / "cm-suricata.yaml", source)
    (tmp_path / "state").mkdir()
    (tmp_path / "tls").mkdir()
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MALCOLM_DIR": str(malcolm),
        "CM_MALCOLM_SRC": str(source),
        "CM_MALCOLM_STATE": str(tmp_path / "state"),
        "CM_MALCOLM_VENV": str(tmp_path / "venv"),
        "CM_SENSOR_TLS": str(tmp_path / "tls"),
        "CM_SENSOR_NAME": "ep-sensor",
        "CM_SENSOR_ADDRESS": "10.42.10.60",
        "CM_MALCOLM_HOST": "10.40.10.10",
    }


def credentials(tmp_path: Path) -> dict[str, str]:
    for name in ("ca.crt", "client.crt", "client.key"):
        (tmp_path / "tls" / name).write_text(f"{name}\n", encoding="utf-8")
    return {"CM_SENSOR_PASSWORD": PASSWORD, "CM_ARKIME_SECRET": SECRET}


def provision(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SENSOR / "cm-sensor-provision")], env=env, capture_output=True, text=True, check=False, timeout=60
    )


def env_file(path: Path) -> dict[str, str]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    return dict(line.split("=", 1) for line in lines)


def test_the_sensor_is_configured_for_the_one_malcolm(tmp_path: Path) -> None:
    env = checkout(tmp_path)
    result = provision({**env, **credentials(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: the sensor ep-sensor is configured and sends to 10.40.10.10" in result.stdout
    malcolm, state = tmp_path / "malcolm", tmp_path / "state"
    assert (state / "provisioned").is_file() and not (state / "idle").exists()

    # The configurator got the settings with the sensor's name and the two addresses.
    given = yaml.safe_load((malcolm / "configured.yaml").read_text(encoding="utf-8"))["configuration"]
    assert (given["pcapNodeName"], given["liveArkimeNodeHost"], given["remoteMalcolmHost"]) == (
        "ep-sensor", "10.42.10.60", "10.40.10.10",
    )
    assert "@CM_" not in (malcolm / "configured.yaml").read_text(encoding="utf-8")

    # Where the sensor sends, whatever the configurator derived.
    config = malcolm / "config"
    assert env_file(config / "beats-common.env") == {"LOGSTASH_HOST": f"10.40.10.10:{LOGSTASH_PORT}", "BEATS_SSL": "true"}
    opensearch = env_file(config / "opensearch.env")
    assert opensearch["OPENSEARCH_PRIMARY"] == "opensearch-remote"
    assert opensearch["OPENSEARCH_URL"] == f"https://10.40.10.10:{OPENSEARCH_PORT}"
    assert opensearch["OPENSEARCH_SSL_CERTIFICATE_VERIFICATION"] == "false"
    assert env_file(config / "upload-common.env")["PCAP_NODE_NAME"] == "ep-sensor"
    assert env_file(config / "arkime-live.env") == {"ARKIME_LIVE_CAPTURE": "true", "ARKIME_LIVE_NODE_HOST": "10.42.10.60"}
    assert env_file(config / "pcap-capture.env")["PCAP_IFACE"] == "phenix"
    assert env_file(config / "suricata.env")["SURICATA_UPDATE_SOURCES"] == "false"
    # The plant network of `cs` is in 192.168.0.0/16.
    assert "192.168.0.0/16" in env_file(config / "zeek.env")["ZEEK_LOCAL_NETS"].split(",")
    assert env_file(config / "zeek-live.env") == {"ZEEK_LB_PROCS_WORKER_DEFAULT": "1"}

    # auth_setup stores Arkime's secret, and this script turns WISE off after it.
    arguments = (malcolm / "auth_setup.args").read_text(encoding="utf-8").splitlines()
    assert arguments == [
        "--auth-noninteractive", f"--auth-arkime-password={SECRET}", "--auth-generate-valkey-password",
        "-f", "docker-compose.yml",
    ]
    assert env_file(config / "arkime-secret.env")["ARKIME_WISE_SERVICE_URL"] == "disabled"

    # The account at Malcolm's OpenSearch, in the format of Malcolm's own script.
    curlrc = malcolm / ".opensearch.primary.curlrc"
    assert curlrc.read_text(encoding="utf-8") == f'user: "sensor:{PASSWORD}"\ninsecure\n'
    # Filebeat's client certificate.
    for name in ("ca.crt", "client.crt", "client.key"):
        assert (malcolm / "filebeat" / "certs" / name).read_text(encoding="utf-8") == f"{name}\n"
    secrets = [curlrc, *config.glob("*.env"), *(malcolm / "filebeat" / "certs").iterdir()]
    assert {path.stat().st_mode & 0o777 for path in secrets} == {0o600}

    # Suricata's settings for mirrored traffic, a Zeek that starts, and no rules of the range.
    assert (malcolm / "suricata" / "include-configs" / "cm-suricata.yaml").is_file()
    assert (malcolm / "zeek" / "custom" / "__load__.zeek").read_text(encoding="utf-8") == ""
    assert not (malcolm / "suricata" / "rules" / "cherrymountain.rules").exists()
    # The directories that the compose file binds and does not create.
    for bound in ("pcap/arkime-live", "zeek-logs/live/spool", "suricata-logs/live", "valkey", "nginx/ca-trust"):
        assert (malcolm / bound).is_dir(), bound

    again = provision({**env, **credentials(tmp_path)})
    assert again.returncode == 0 and "already provisioned" in again.stdout


def test_a_sensor_with_no_malcolm_stays_idle(tmp_path: Path) -> None:
    """An experiment of one site alone: the app gives no credentials."""
    env = checkout(tmp_path)
    result = provision(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "IDLE: this experiment has no Malcolm" in result.stdout
    state = tmp_path / "state"
    assert (state / "idle").stat().st_mode & 0o777 == 0o644
    assert not (state / "provisioned").exists()
    assert not (tmp_path / "malcolm" / "configured.yaml").exists()

    start = subprocess.run(
        ["bash", str(SENSOR / "cm-sensor-start")], env=env, capture_output=True, text=True, check=False, timeout=30
    )
    assert start.returncode == 0 and start.stdout.startswith("cm-sensor-start: IDLE: ")
    health = subprocess.run(
        ["bash", str(SENSOR / "cm-sensor-health")], env=env, capture_output=True, text=True, check=False, timeout=30
    )
    assert health.returncode == 1 and health.stdout.startswith("UNHEALTHY: the sensor is idle")

    # With credentials at a later boot, the sensor is configured.
    result = provision({**env, **credentials(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "provisioned").is_file() and not (state / "idle").exists()


def test_a_part_of_the_credentials_is_an_error(tmp_path: Path) -> None:
    env = checkout(tmp_path)
    full = credentials(tmp_path)
    (tmp_path / "tls" / "client.key").unlink()
    result = provision({**env, **full})
    assert result.returncode == 1
    assert f"FAIL: a part of the sensor's credentials is missing: {tmp_path / 'tls' / 'client.key'}" in result.stdout
    result = provision({**env, "CM_SENSOR_PASSWORD": PASSWORD})
    assert result.returncode == 1 and "CM_ARKIME_SECRET" in result.stdout
    assert not (tmp_path / "state" / "provisioned").exists() and not (tmp_path / "state" / "idle").exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"CM_SENSOR_NAME": ""}, "CM_SENSOR_NAME in /etc/default/cm-sensor must be a host name"),
        ({"CM_SENSOR_NAME": "ep sensor"}, "CM_SENSOR_NAME in /etc/default/cm-sensor must be a host name"),
        ({"CM_SENSOR_ADDRESS": "10.42.10"}, "CM_SENSOR_ADDRESS in /etc/default/cm-sensor must be an IPv4 address"),
        ({"CM_MALCOLM_HOST": "malcolm"}, "CM_MALCOLM_HOST in /etc/default/cm-sensor must be an IPv4 address"),
        ({"CM_SENSOR_PASSWORD": "short"}, "CM_SENSOR_PASSWORD must be 8 to 72 letters and digits"),
        ({"CM_SENSOR_PASSWORD": 'pass"word:1'}, "CM_SENSOR_PASSWORD must be 8 to 72 letters and digits"),
        ({"CM_ARKIME_SECRET": "secret|with&sed"}, "CM_ARKIME_SECRET must be 16 to 128 letters and digits"),
        ({"FAKE_PROFILE": "malcolm"}, "config/process.env does not have MALCOLM_PROFILE=hedgehog"),
    ],
)
def test_the_sensor_refuses_a_value_that_would_break_a_file(tmp_path: Path, change: dict[str, str], reason: str) -> None:
    env = checkout(tmp_path)
    result = provision({**env, **credentials(tmp_path), **change})
    assert result.returncode == 1
    assert f"cm-sensor-provision: FAIL: {reason}" in result.stdout
    assert not (tmp_path / "state" / "provisioned").exists()
    # Nothing went to Malcolm's auth_setup.
    assert not (tmp_path / "malcolm" / "auth_setup.args").exists()


def test_the_baseline_detections_are_installed_when_a_topology_gives_them(tmp_path: Path) -> None:
    """No topology does today: the rules name the Front Range hosts by address."""
    env = checkout(tmp_path)
    source = Path(env["CM_MALCOLM_SRC"])
    shutil.copy(MALCOLM / "cherrymountain.rules", source)
    shutil.copytree(MALCOLM / "zeek", source / "zeek")
    result = provision({**env, **credentials(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    malcolm = tmp_path / "malcolm"
    assert (malcolm / "suricata" / "rules" / "cherrymountain.rules").is_file()
    assert "@load ./cherrymountain" in (malcolm / "zeek" / "custom" / "__load__.zeek").read_text(encoding="utf-8")


# --- the host firewall ------------------------------------------------------------------


def acl(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run cm-sensor-acl with a stand-in nft that keeps the ruleset."""
    nft = tmp_path / "nft"
    nft.write_text(f'#!/bin/sh\ncat > "{tmp_path / "ruleset"}"\n', encoding="utf-8")
    nft.chmod(0o755)
    ip = tmp_path / "bin" / "ip"
    ip.parent.mkdir(exist_ok=True)
    ip.write_text('#!/bin/sh\nprintf \'%s\' "${FAKE_IP:-}"\n', encoding="utf-8")
    ip.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SENSOR / "cm-sensor-acl")],
        env={
            **os.environ, "PATH": f"{ip.parent}:{os.environ['PATH']}", "CM_NFT": str(nft),
            "CM_SENSOR_ADDRESS": "10.42.10.60", "CM_MALCOLM_HOST": "10.40.10.10", **env,
        },
        capture_output=True, text=True, check=False, timeout=30,
    )
    ruleset = tmp_path / "ruleset"
    return result, ruleset.read_text(encoding="utf-8") if ruleset.exists() else ""


#: `ip -o -4 addr show` on a sensor: the kernel's names, not those of the topology.
ADDRESSES = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "2: ens1    inet 10.42.10.60/24 brd 10.42.10.255 scope global ens1\\       valid_lft forever preferred_lft forever\n"
    "4: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever\n"
)


def test_the_viewer_answers_malcolm_only_and_the_mirror_takes_gre_only(tmp_path: Path) -> None:
    result, ruleset = acl(tmp_path, FAKE_IP=ADDRESSES)
    assert result.returncode == 0, result.stdout + result.stderr
    rules = [line.strip() for line in ruleset.splitlines()]
    # Arkime's viewer trusts a header for the user's name: only Malcolm may reach it.
    assert f'iifname "ens1" tcp dport {VIEWER_PORT} ip saddr != 10.40.10.10 counter drop' in rules
    assert f'iifname "ens1" meta nfproto ipv6 tcp dport {VIEWER_PORT} counter drop' in rules
    # The order decides: the viewer's two rules stand before the interface's accept.
    assert rules.index(f'iifname "ens1" tcp dport {VIEWER_PORT} ip saddr != 10.40.10.10 counter drop') < rules.index(
        'iifname "ens1" accept'
    )
    # Every other interface but Docker's is a mirror interface: GRE in, and no way through.
    chain = rules[rules.index("chain input {") : rules.index("chain forward {")]
    assert chain[-4:-2] == ["meta l4proto gre accept", "counter drop"]
    assert 'iifname != "ens1" iifname != "docker0" iifname != "br-*" counter drop' in rules
    assert 'oifname != "ens1" oifname != "docker0" oifname != "br-*" counter drop' in rules
    # A second run replaces the table.
    assert rules[:2] == ["table inet cm_sensor", "delete table inet cm_sensor"]
    assert f"PASS: {VIEWER_PORT}/tcp answers 10.40.10.10 only" in result.stdout


def test_the_firewall_waits_for_the_sensors_address(tmp_path: Path) -> None:
    """phenix sets the address during boot. Until then the unit tries again."""
    result, ruleset = acl(tmp_path, FAKE_IP=ADDRESSES.replace("10.42.10.60", "10.42.10.61"))
    assert result.returncode == 1 and not ruleset
    assert "FAIL: no interface has the address 10.42.10.60 yet" in result.stdout
    unit = script("cm-sensor-acl.service")
    assert re.search(r"^Restart=on-failure$", unit, re.MULTILINE) and re.search(r"^Before=cm-sensor\.service$", unit, re.MULTILINE)
    for value in ("", "malcolm", "10.40.10.10; flush ruleset"):
        result, ruleset = acl(tmp_path, FAKE_IP=ADDRESSES, CM_MALCOLM_HOST=value)
        assert result.returncode == 1 and not ruleset
        assert "FAIL: CM_MALCOLM_HOST in /etc/default/cm-sensor must be an IPv4 address" in result.stdout


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
def test_the_firewall_parses(tmp_path: Path) -> None:
    _, ruleset = acl(tmp_path, FAKE_IP=ADDRESSES)
    # The first two lines replace a table of an earlier run, which a check has no use for.
    rules = tmp_path / "rules.nft"
    rules.write_text(ruleset, encoding="utf-8")
    result = subprocess.run(["nft", "-c", "-f", str(rules)], capture_output=True, text=True, check=False)
    if "Operation not permitted" in result.stderr:
        pytest.skip("nft cannot load rules here")
    assert result.returncode == 0, result.stderr


# --- cm-sensor-health -------------------------------------------------------------------

HEALTH_DOCKER = """#!/bin/sh
for service in valkey filebeat arkime-live zeek-live suricata-live; do
  [ "$service" = "${FAKE_DOWN:-}" ] && continue
  status='Up 3 hours'
  [ "$service" = "${FAKE_UNHEALTHY:-}" ] && status='Up 3 hours (unhealthy)'
  echo "malcolm-${service}-1|${status}"
done
"""
HEALTH_CURL = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_CURL_ARGS"
[ -n "${FAKE_COUNT:-}" ] || exit 7
printf '%s' "$FAKE_COUNT"
"""


def health(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", HEALTH_DOCKER), ("curl", HEALTH_CURL), ("sleep", "#!/bin/sh\nexit 0\n")):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    port = tmp_path / "net" / "phenix"
    (port / "statistics").mkdir(parents=True, exist_ok=True)
    (port / "flags").write_text(env.pop("FAKE_FLAGS", "0x1103") + "\n", encoding="utf-8")
    (port / "statistics" / "rx_packets").write_text("100\n", encoding="utf-8")
    # The second read of the counter finds more packets: `sleep` is where they arrive.
    grow = env.pop("FAKE_PACKETS", "40")
    (bin_dir / "sleep").write_text(
        f'#!/bin/sh\necho $((100 + {grow})) > "{port / "statistics" / "rx_packets"}"\n', encoding="utf-8"
    )
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(SENSOR / "cm-sensor-health")],
        env={
            **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CM_MALCOLM_STATE": str(state),
            "MALCOLM_DIR": str(tmp_path / "malcolm"), "CM_SYSFS_NET": str(tmp_path / "net"),
            "CM_SENSOR_NAME": "ep-sensor", "CM_MALCOLM_HOST": "10.40.10.10",
            "FAKE_CURL_ARGS": str(tmp_path / "curl.args"), "FAKE_COUNT": '{"count": 12}', **env,
        },
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_sensor_health_needs_sessions_at_malcolm(tmp_path: Path) -> None:
    """Running containers are not enough: an arkime-live that cannot sign in to
    Malcolm's OpenSearch waits for it for ever and still reports healthy."""
    result = health(tmp_path)
    assert result.returncode == 1 and result.stdout.startswith("UNHEALTHY: the sensor is not provisioned")
    (tmp_path / "state" / "provisioned").touch()
    result = health(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("HEALTHY: 40 packets on phenix in 3 s, and 12 Arkime sessions of ep-sensor")
    # The question goes to Malcolm's OpenSearch, with the sensor's account, and is about this sensor.
    asked = (tmp_path / "curl.args").read_text(encoding="utf-8")
    assert f"https://10.40.10.10:{OPENSEARCH_PORT}/arkime_sessions3-*/_count" in asked
    assert f"{tmp_path / 'malcolm'}/.opensearch.primary.curlrc" in asked
    assert '{"term":{"node":"ep-sensor"}}' in asked
    for env, reason in (
        ({"FAKE_DOWN": "arkime-live"}, "container malcolm-arkime-live-1 is not running"),
        ({"FAKE_UNHEALTHY": "filebeat"}, "container malcolm-filebeat-1 is unhealthy"),
        ({"FAKE_FLAGS": "0x1003"}, "capture port phenix is not in promiscuous mode"),
        ({"FAKE_PACKETS": "0"}, "capture port phenix received no packets in 3 s"),
        ({"FAKE_COUNT": ""}, "Malcolm's OpenSearch (10.40.10.10:9200) did not answer"),
        ({"FAKE_COUNT": "<html>401 Authorization Required</html>"}, "Malcolm's OpenSearch (10.40.10.10:9200) refused the count query"),
        ({"FAKE_COUNT": '{"count": 0}'}, "Malcolm has no Arkime sessions of ep-sensor from the last 15 minutes"),
    ):
        result = health(tmp_path, **env)
        assert result.returncode == 1 and result.stdout.startswith(f"UNHEALTHY: {reason}"), result.stdout


# --- the units and the startup hook -----------------------------------------------------


def test_the_sensor_starts_behind_its_firewall_and_after_its_configuration() -> None:
    unit = script("cm-sensor.service")
    (after,) = re.findall(r"^After=(.*)$", unit, re.MULTILINE)
    assert {"cm-sensor-acl.service", "cm-sensor-provision.service", "cm-malcolm-capture.service"} <= set(after.split())
    # The first boot's configuration can fail and succeed later: the unit tries again.
    assert not re.search(r"^ConditionPathExists=", unit, re.MULTILINE)
    assert re.search(r"^Restart=on-failure$", unit, re.MULTILINE)
    provisioning = script("cm-sensor-provision.service")
    assert re.search(r"^After=.*\bcm-load-images\.service\b", provisioning, re.MULTILINE)
    # Only the unit that runs as root reads the credentials.
    assert "EnvironmentFile=-/etc/default/cm-sensor-credentials" in provisioning
    assert "cm-sensor-credentials" not in unit and "cm-sensor-credentials" not in script("cm-sensor-acl.service")


def test_the_startup_hook_enables_units_that_the_sensor_has() -> None:
    (line,) = re.findall(r"^systemctl enable --now --no-block (.*)$", script("66-sensor.sh"), re.MULTILINE)
    enabled = set(line.split())
    assert enabled == {"cm-sensor-acl.service", "cm-load-images.service", "cm-sensor-provision.service", "cm-sensor.service"}
    for code in ("ep", "cs", "fc"):
        topology = yaml.safe_load((CONFIGS / f"topology-site-{code}.yml").read_text(encoding="utf-8"))
        sensor = next(n for n in topology["spec"]["nodes"] if n["general"]["hostname"] == f"{code}-sensor")
        targets = {injection["dst"] for injection in sensor["injections"]}
        for unit in (*enabled, "cm-malcolm-capture.service"):
            assert f"/etc/systemd/system/{unit}" in targets, (code, unit)
        # Each program that a unit or the hook runs is injected too.
        for name in ("acl", "provision", "start", "stop", "health"):
            assert f"/usr/local/sbin/cm-sensor-{name}" in targets, (code, name)
        assert {"/usr/local/sbin/cm-load-images", "/usr/local/sbin/cm-malcolm-capture"} <= targets
        assert "/etc/default/cm-sensor" in targets
        assert "/opt/cherrymountain/malcolm/malcolm-config-sensor.yaml" in targets
        assert "/opt/cherrymountain/malcolm/cm-suricata.yaml" in targets


# --- what malcolm does for the sensors --------------------------------------------------


def malcolm_provision() -> str:
    return (MALCOLM / "cm-malcolm-provision").read_text(encoding="utf-8")


def test_malcolm_opens_the_two_ports_of_the_sensors_and_no_other() -> None:
    extra = yaml.safe_load((MALCOLM / "malcolm-config-sensors.yaml").read_text(encoding="utf-8"))["configuration"]
    assert not set(extra) - MALCOLM_SETTINGS
    # `yes` would also open Filebeat's TCP listener (and, without `customize`, the two are not read).
    assert extra == {"openPortsSelection": "customize", "exposeLogstash": True, "exposeOpenSearch": True}
    for profile in ("full", "slim"):
        settings = yaml.safe_load((MALCOLM / f"malcolm-config-{profile}.yaml").read_text(encoding="utf-8"))["configuration"]
        # Without a sensor, Malcolm opens neither.
        assert not set(extra) & set(settings)
    soc = yaml.safe_load((CONFIGS / "topology-soc.yml").read_text(encoding="utf-8"))
    malcolm = next(n for n in soc["spec"]["nodes"] if n["general"]["hostname"] == "malcolm")
    assert "/opt/cherrymountain/malcolm/malcolm-config-sensors.yaml" in {i["dst"] for i in malcolm["injections"]}


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_the_sensors_settings_go_on_top_of_a_profile(tmp_path: Path, profile: str) -> None:
    """The program that cm-malcolm-provision merges the two files with."""
    program = re.search(r"<< 'MERGE'\n(.*?)\nMERGE\n", malcolm_provision(), re.DOTALL)
    assert program is not None
    base = MALCOLM / f"malcolm-config-{profile}.yaml"
    result = subprocess.run(
        ["python3", "-I", "-", str(base), str(MALCOLM / "malcolm-config-sensors.yaml")],
        input=program.group(1), capture_output=True, text=True, check=False, timeout=30,
        # pyyaml is in this environment's packages, which -I keeps.
    )
    assert result.returncode == 0, result.stderr
    merged = yaml.safe_load(result.stdout)["configuration"]
    before = yaml.safe_load(base.read_text(encoding="utf-8"))["configuration"]
    assert merged == {**before, "openPortsSelection": "customize", "exposeLogstash": True, "exposeOpenSearch": True}
    assert list(merged)[: len(before)] == list(before)


def test_malcolm_and_a_sensor_agree_on_the_account_and_the_secret() -> None:
    malcolm, sensor = malcolm_provision(), script("cm-sensor-provision")
    for line in (
        'SENSOR_USER="${CM_SENSOR_USER:-sensor}"',
        "=~ ^[A-Za-z0-9]{8,72}$ ]]",
        "=~ ^[A-Za-z0-9]{16,128}$ ]]",
        "${CM_SENSOR_TLS:-/etc/cherrymountain/sensor-tls}",
    ):
        assert line in malcolm and line in sensor, line
    # The same secret on both sides: Malcolm's Arkime signs its request to a sensor with it.
    assert 'arkime_secret="$CM_ARKIME_SECRET"' in malcolm and '--auth-arkime-password="$arkime_secret"' in malcolm
    assert '--auth-arkime-password="$secret"' in sensor and 'secret="$CM_ARKIME_SECRET"' in sensor
    # The account is a user of nginx, which stands before OpenSearch on 9200/tcp. The
    # password comes on stdin, and bcrypt reads 72 characters of it.
    assert 'printf \'%s\' "$CM_SENSOR_PASSWORD" | htpasswd -iB nginx/htpasswd "$SENSOR_USER"' in malcolm
    assert "printf 'user: \"%s:%s\"\\ninsecure\\n' \"$SENSOR_USER\" \"$password\"" in sensor


def test_malcolm_keeps_the_certificates_that_the_sensors_trust() -> None:
    """auth_setup makes a CA and a pair of its own where logstash/certs/server.key or
    filebeat/certs/client.key is missing (scripts/control.py). The sensors hold a client
    certificate of the app's CA, so the app's files must be there first."""
    text = malcolm_provision()
    server = text.index('install -m 0600 "${SENSOR_TLS}/ca.crt" "${SENSOR_TLS}/server.crt" "${SENSOR_TLS}/server.key" logstash/certs/')
    client = text.index('install -m 0600 "${SENSOR_TLS}/ca.crt" "${SENSOR_TLS}/client.crt" "${SENSOR_TLS}/client.key" filebeat/certs/')
    auth_setup = text.index("./scripts/auth_setup")
    assert server < auth_setup and client < auth_setup
    assert "--auth-generate-fwcerts" not in text
    assert text.index('cmp -s "${SENSOR_TLS}/server.key" logstash/certs/server.key') > auth_setup
    # A sensor's Filebeat uses the same three names.
    assert 'install -m 0600 "${TLS}/ca.crt" "${TLS}/client.crt" "${TLS}/client.key" filebeat/certs/' in script("cm-sensor-provision")


def test_malcolm_without_sensors_is_the_single_site_malcolm() -> None:
    """All of what the app gives for the sensors, or none of it."""
    text = malcolm_provision()
    assert 'arkime_secret="$password"' in text
    block = text[text.index("# --- the sensors of other sites") : text.index('chown -R "${MALCOLM_UID}:${MALCOLM_UID}" "${MALCOLM_DIR}"')]
    assert block.index("sensors=false") < block.index('if [ "${#sensor_given[@]}" -gt 0 ]; then')
    assert "FAIL: a part of what the sensors need is missing" in block
    for name in ("ca.crt", "server.crt", "server.key", "client.crt", "client.key"):
        assert name in block
    # Each later step for the sensors is behind the same switch.
    assert text.count('if [ "$sensors" = true ]; then') == 3
