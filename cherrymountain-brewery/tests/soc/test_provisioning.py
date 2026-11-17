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

import json
import os
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from soc_paths import CONFIGS, INJECTS, ROOT

MALCOLM: Path = INJECTS / "malcolm"
WAZUH: Path = INJECTS / "wazuh"
IMAGES: Path = ROOT / "phenix-images"
AGENT_SCRIPT: Path = ROOT / "apps/cherrymountain_apps/cherrymountain/templates/scripts/80-cm-wazuh-agent.sh"

#: The env file of Malcolm 26.10.0 that defines each variable cm-malcolm-provision
#: sets on top of the configurator's output (config/<file>.env.example). Compose reads
#: a service's env files in order and the last one wins, so a variable set in any
#: other file is overridden by the blank or default value here. A variable Malcolm
#: does not define (ZEEK_LB_PROCS_WORKER_DEFAULT, PYTHONUNBUFFERED) is absent.
MALCOLM_ENV_HOME: dict[str, str] = {
    "PCAP_PIPELINE_VERBOSITY": "upload-common",
    "ZEEK_LOCAL_NETS": "zeek",
    "PCAP_IFACE": "pcap-capture",
    "PCAP_ROTATE_MINUTES": "pcap-capture",
    "SURICATA_UPDATE_SOURCES": "suricata",
}
#: Every setting Malcolm 26.10.0's configurator knows: the keys of
#: `scripts/configure --non-interactive --configure --defaults --dry-run
#: --export-malcolm-config-file`. It takes a settings file with an unknown key
#: without complaint and ignores the key, so a misspelt setting would go unnoticed.
MALCOLM_SETTINGS: frozenset[str] = frozenset(
    """
    arkimeAllowWiseConfig arkimeExposeWise arkimeFreeSpaceG arkimeManagePCAP arkimeWiseUrl autoArkime
    autoFreq autoOui autoSuricata autoZeek autoZeekCommunityID auxFwAide auxFwAuditlog auxFwCPU auxFwDf
    auxFwDisk auxFwKmsg auxFwMem auxFwNetwork auxFwSystemd auxFwThermal captureLiveNetworkTraffic
    captureStats cleanUpOldArtifacts cleanUpOldIndices containerNetworkName dashboardsDarkMode
    dashboardsUrl dockerOrchestrationMode exposeFilebeatTcp exposeLogstash exposeOpenSearch exposeSFTP
    extraTags extractedFileMaxPercentThreshold extractedFileMaxSizeThreshold
    fileCarveHttpServeEncryptKey fileCarveHttpServer fileCarveHttpServerZip fileCarveMode
    filePreserveMode fileScanRuleUpdate filebeatTcpDefaults filebeatTcpLogFormat
    filebeatTcpParseDropField filebeatTcpParseSourceField filebeatTcpParseTargetField filebeatTcpTag
    filescanLogDir imageArch indexDir indexManagementHistoryInWeeks indexManagementHotWarm
    indexManagementOptimizationTimePeriod indexManagementOptimizeSessionSegments indexManagementPolicy
    indexManagementReplicas indexManagementSpiDataRetention indexPruneNameSort indexPruneThreshold
    indexSnapshotDir liveArkime liveArkimeCompressionLevel liveArkimeCompressionType liveArkimeNodeHost
    liveSuricata liveZeek logstashHost logstashNetBoxEnrichedDatasets lsMemory lsWorkers malSysPruneLogs
    malSysPrunePcap malcolmIcs malcolmProfile malcolmRestartPolicy netboxAutoPopulate
    netboxAutoPopulateSubnetFilter netboxLogstashAutoCreatePrefix netboxLogstashEnrich netboxMode
    netboxSiteName netboxUrl nginxResolverIpv4 nginxResolverIpv6 nginxSSL openPortsSelection
    opensearchPrimaryMode opensearchPrimarySslVerify opensearchPrimaryUrl opensearchSecondaryMode
    opensearchSecondarySslVerify opensearchSecondaryUrl osMemory pcapDir pcapFilter pcapIface
    pcapNetSniff pcapNodeName pcapTcpDump pipelineEnabled pipelineScanners pipelineWorkers
    processGroupId processUserId reachbackRequestAcl remoteMalcolmHost reverseDns runtimeBin
    secondaryDocumentStore suricataDisableSids suricataLogDir suricataRuleUpdate syslogTcpPort
    syslogUdpPort traefikEntrypoint traefikHost traefikLabels traefikOpenSearchHost traefikResolver
    tweakIface useDefaultStorageLocations zeekICSBestGuess zeekIntelCronExpression zeekIntelFeedSince
    zeekIntelItemExpiration zeekIntelLive zeekIntelOffline zeekIntelOnStartup zeekLogDir
    zeekPullIntelligenceFeeds
    """.split()
)
#: The image of each compose service of Malcolm 26.10.0 that a profile starts
#: (ghcr.io/idaholab/malcolm/<image>).
MALCOLM_SERVICE_IMAGE: dict[str, str] = {
    "opensearch": "opensearch", "dashboards-helper": "dashboards-helper", "dashboards": "dashboards",
    "logstash": "logstash-oss", "filebeat": "filebeat-oss", "arkime": "arkime", "pcap-capture": "pcap-capture",
    "zeek": "zeek", "zeek-live": "zeek", "filescan": "filescan", "pcap-monitor": "pcap-monitor",
    "upload": "file-upload", "htadmin": "htadmin", "valkey": "valkey", "valkey-cache": "valkey", "api": "api",
    "nginx-proxy": "nginx-proxy", "suricata": "suricata", "suricata-live": "suricata",
    "strelka-backend": "strelka-backend", "strelka-frontend": "strelka-frontend",
    "strelka-manager": "strelka-manager", "freq": "freq", "netbox": "netbox", "postgres": "postgresql",
}
#: Bind-mount sources of Malcolm 26.10.0's compose file that are not in its checkout
#: (create_host_path: false, so a missing one fails `compose up`), by service.
MALCOLM_BIND_SOURCES: dict[str, str] = {"valkey": "valkey", "postgres": "postgres", "netbox": "netbox/media"}

#: The <client> block of the ossec.conf that the wazuh-agent 4.14.10 package writes at
#: install time, the line of its inventory settings that a grep for <groups> finds,
#: and its log readers: where journalctl exists, the package's add_localfiles.sh adds
#: a journald reader (and leaves out auth.log, syslog, and kern.log).
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

  <localfile>
    <log_format>journald</log_format>
    <location>journald</location>
  </localfile>

  <localfile>
    <log_format>syslog</log_format>
    <location>/var/ossec/logs/active-responses.log</location>
  </localfile>
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


def malcolm_settings(profile: str) -> dict[str, Any]:
    """The profile's settings file for Malcolm's configurator."""
    data = yaml.safe_load((MALCOLM / f"malcolm-config-{profile}.yaml").read_text(encoding="utf-8"))
    assert set(data) == {"configuration"}
    return data["configuration"]


def malcolm_services(profile: str) -> list[str]:
    """The compose services cm-malcolm-start brings up for a profile."""
    out = subprocess.run(
        ["bash", "-c", f'. "{MALCOLM / "profiles.sh"}"; cm_malcolm_services {profile}'],
        capture_output=True, text=True, check=True,
    ).stdout
    return ["opensearch", *out.split()]


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_malcolm_settings_are_ones_the_configurator_knows(profile: str) -> None:
    settings = malcolm_settings(profile)
    assert not set(settings) - MALCOLM_SETTINGS, "the configurator ignores an unknown key without a word"
    # The provisioning script hands the configurator this file, and the topology injects it.
    assert 'settings="${SRC}/malcolm-config-${profile}.yaml"' in provision()
    assert "./scripts/configure \\\n  --non-interactive --configure --skip-splash --import-malcolm-config-file" in provision()
    soc = yaml.safe_load((CONFIGS / "topology-soc.yml").read_text(encoding="utf-8"))
    malcolm = next(n for n in soc["spec"]["nodes"] if n["general"]["hostname"] == "malcolm")
    assert f"/opt/cherrymountain/malcolm/malcolm-config-{profile}.yaml" in {i["dst"] for i in malcolm["injections"]}


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_arkime_gets_its_pcap_from_the_capture_container(profile: str) -> None:
    """Arkime's own live capture (arkime-live) runs on the host network and looks up
    `opensearch`, a name only Malcolm's container network has: with a local OpenSearch
    it waits for it for ever, still reports healthy, and Arkime has no session and no
    pcap of the mirrored traffic. Malcolm's configurator offers that mode only to a
    sensor that forwards elsewhere. Here pcap-capture writes rotating pcap files and
    the arkime container indexes them; Zeek and Suricata read the port themselves."""
    settings = malcolm_settings(profile)
    assert settings["captureLiveNetworkTraffic"] is True and settings["pcapIface"] == ["phenix"]
    assert "liveArkime" not in settings and "opensearchPrimaryMode" not in settings
    assert (settings["pcapTcpDump"], settings["pcapNetSniff"]) == (True, False)
    assert settings["liveZeek"] is True
    services = malcolm_services(profile)
    assert "pcap-capture" in services and "arkime" in services and "pcap-monitor" in services
    assert "arkime-live" not in services
    assert "ARKIME_LIVE_CAPTURE" not in provision()
    assert "set_env config/pcap-capture.env PCAP_ROTATE_MINUTES 2" in provision()


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_the_services_of_a_profile_match_its_settings(profile: str) -> None:
    """A profile starts a subset of Malcolm's services. A setting that needs a service
    the profile leaves out (or the reverse) gives a pipeline that waits for it."""
    settings = malcolm_settings(profile)
    services = set(malcolm_services(profile))
    live_suricata = settings["liveSuricata"]
    assert ("suricata-live" in services) == live_suricata
    assert ("suricata" in services) == settings.get("autoSuricata", True) == live_suricata
    netbox = settings["netboxMode"] == "local"
    assert ({"netbox", "postgres"} <= services) == netbox and bool({"netbox", "postgres"} & services) == netbox
    assert settings["netboxLogstashEnrich"] == netbox
    scanning = settings.get("pipelineEnabled", True)
    strelka = {"strelka-backend", "strelka-frontend", "strelka-manager"}
    assert (strelka <= services) == scanning and bool(strelka & services) == scanning
    # Zeek extracts files only where something scans them.
    assert (settings["fileCarveMode"] != "none") == scanning
    assert ("freq" in services) == settings.get("autoFreq", True)
    # nginx will not start without its fixed upstreams.
    assert {"filescan", "dashboards", "arkime", "api", "htadmin", "upload"} <= services
    # No single sign-on, so no keycloak.
    assert "keycloak" not in services


def test_the_image_holds_every_service_of_the_full_profile() -> None:
    """The range has no registry to pull from: a service whose image is not in the VM
    image fails `compose up` for the whole profile."""
    build = (IMAGES / "cm-malcolm.yml").read_text(encoding="utf-8")
    names = re.search(r"^\s*for name in ([\w -]+); do$", build, re.MULTILINE)
    assert names is not None
    in_image = set(names.group(1).split())
    needed = {MALCOLM_SERVICE_IMAGE[s] for s in malcolm_services("full")}
    assert needed == in_image, f"missing {needed - in_image}, unused {in_image - needed}"
    assert {MALCOLM_SERVICE_IMAGE[s] for s in malcolm_services("slim")} <= in_image
    assert f"({len(in_image)} of the full profile" in build.replace("the ", "", 1) or f"the {len(in_image)} of the full profile" in build


def test_malcolm_containers_come_back_by_themselves() -> None:
    """Malcolm's compose file has restart "no": a container that dies during an
    exercise stays down. The configurator sets the policy its installation examples
    use for every service."""
    for profile in ("full", "slim"):
        assert malcolm_settings(profile)["malcolmRestartPolicy"] == "unless-stopped"


def test_one_zeek_worker() -> None:
    """cherrymountain.zeek keeps state between connections in globals, which Zeek's
    workers do not share, and Malcolm's zeek-live starts two workers unless told (the
    variable is in no env example, so it is appended to zeek-live.env, which the
    zeek-live service reads)."""
    assert re.search(r"^set_env config/zeek-live\.env ZEEK_LB_PROCS_WORKER_DEFAULT 1$", provision(), re.MULTILINE)


def heaps_for(tmp_path: Path, gigabytes: int, profile: str) -> str:
    """cm_malcolm_heaps on a VM of that size (what Linux reports: about 2 % less)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    pages = int(gigabytes * 1024 * 1024 * 1024 * 0.98) // 4096
    (bin_dir / "getconf").write_text(f'#!/bin/sh\ncase "$1" in _PHYS_PAGES) echo {pages} ;; PAGE_SIZE) echo 4096 ;; esac\n')
    (bin_dir / "getconf").chmod(0o755)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", f'. "{MALCOLM / "profiles.sh"}"; cm_malcolm_heaps {profile}'],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_the_heaps_follow_the_memory_of_the_vm(tmp_path: Path) -> None:
    """OpenSearch gets the heap of Malcolm's example configuration (10 GB) on the 48 GB
    sensor of the topology, and less on Malcolm's 24 GB minimum, where 10 GB would
    leave too little for the other containers."""
    soc = yaml.safe_load((CONFIGS / "topology-soc.yml").read_text(encoding="utf-8"))
    malcolm = next(n for n in soc["spec"]["nodes"] if n["general"]["hostname"] == "malcolm")
    assert malcolm["hardware"]["memory"] == 48 * 1024 and malcolm["hardware"]["vcpus"] == 8
    assert heaps_for(tmp_path, 48, "full") == "10g 3g"
    assert heaps_for(tmp_path, 32, "full") == "10g 3g"
    assert heaps_for(tmp_path, 24, "full") == "6g 3g"
    assert heaps_for(tmp_path, 12, "slim") == "3g 2g"
    # The script must notice a size it cannot read: a here-string would hide the failure.
    assert 'heaps=$(cm_malcolm_heaps "$profile")' in provision()


def test_the_image_has_the_host_settings_of_malcolms_installer() -> None:
    """The sysctl values of Malcolm 26.10.0's installer
    (scripts/installer/platforms/utils/linux_tweaks.py)."""
    build = (IMAGES / "cm-malcolm.yml").read_text(encoding="utf-8")
    block = build[build.index("/etc/sysctl.d/99-cm-malcolm.conf") : build.index("/etc/security/limits.d/99-cm-malcolm.conf")]
    values = dict(re.findall(r"^\s*([\w.-]+) = (\d+)$", block, re.MULTILINE))
    assert values == {
        "fs.file-max": "2097152", "fs.inotify.max_user_watches": "131072", "fs.inotify.max_queued_events": "131072",
        "fs.inotify.max_user_instances": "512", "vm.max_map_count": "524288", "vm.swappiness": "1",
        "vm.dirty_background_ratio": "5", "vm.dirty_ratio": "10", "vm.overcommit_memory": "1",
        "net.core.somaxconn": "65535", "net.ipv4.tcp_retries2": "5",
    }


def test_the_suricata_include_is_installed_and_sets_single_values() -> None:
    """Malcolm appends every suricata/include-configs/*.yaml to suricata.yaml. A nested
    block in an included file replaces the whole section, so the file sets dotted keys."""
    text = provision()
    assert 'cp "${SRC}/cm-suricata.yaml" suricata/include-configs/cm-suricata.yaml' in text
    assert "suricata/include-configs" in text[text.index("mkdir -p zeek/custom") :].splitlines()[0]
    soc = yaml.safe_load((CONFIGS / "topology-soc.yml").read_text(encoding="utf-8"))
    malcolm = next(n for n in soc["spec"]["nodes"] if n["general"]["hostname"] == "malcolm")
    assert any(i["dst"] == "/opt/cherrymountain/malcolm/cm-suricata.yaml" for i in malcolm["injections"])
    settings = yaml.safe_load((MALCOLM / "cm-suricata.yaml").read_text().split("---", 1)[1])
    assert settings == {"vlan.use-for-tracking": False, "stream.async-oneside": True}


def test_expect_skips_signatures_on_the_slim_profile(tmp_path: Path) -> None:
    """The slim profile runs no Suricata: a signature is skipped there, a notice is not."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(EXPECT_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    (tmp_path / "state").mkdir()
    for profile, lines in (("slim", ["SKIP", "PASS"]), ("full", ["PASS", "PASS"])):
        (tmp_path / "state" / "profile").write_text(f"{profile}\n")
        proc = subprocess.run(
            ["bash", str(MALCOLM / "cm-malcolm-expect"), "CM WireGuard handshake initiation", "CherryMountain::PLC_REST_From_EWS"],
            env={
                **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_QUERY": str(tmp_path / "query.json"),
                "CM_MALCOLM_STATE": str(tmp_path / "state"),
            },
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert [line.split(":")[0] for line in proc.stdout.splitlines()] == lines, proc.stdout
    # The static SoH list has no container that only one profile runs.
    scenario = (CONFIGS / "scenario-full.yml").read_text(encoding="utf-8")
    assert not re.search(r"^ *- malcolm-suricata", scenario, re.MULTILINE)


def test_the_selftest_asks_for_its_own_run() -> None:
    """The pcap's timestamps are fixed, so the probe's source port tells one run's
    records from another's: the generator takes it and both searches ask for it."""
    import cm_selftest_pcap  # phenix-injects/malcolm is on the path (soc_paths)

    probes = [d for d in cm_selftest_pcap.datagrams(23456) if d.src == cm_selftest_pcap.PROBE]
    assert probes and {d.sport for d in probes} == {23456} and {d.dport for d in probes} == {47808}
    script = (MALCOLM / "cm-malcolm-selftest").read_text()
    assert 'cm_selftest_pcap.py" "$staged" "$sport"' in script
    for query in ("zeek_query", "suricata_query"):
        line = next(line for line in script.splitlines() if line.startswith(f"{query}="))
        assert '{"term":{"source.port":%s}}' in line and line.rstrip().endswith('"$sport")'), query


def test_malcolm_start_waits_for_a_usable_cluster() -> None:
    """An OpenSearch error body has a "status" too (a number): only yellow or green
    is ready."""
    script = (MALCOLM / "cm-malcolm-start").read_text()
    pattern = re.search(r"grep -Eq '([^']+)'", script)
    assert pattern is not None
    for body, ready in (
        ('{"cluster_name":"x","status":"yellow","timed_out":false}', True),
        ('{"cluster_name":"x","status" : "green"}', True),
        ('{"cluster_name":"x","status":"red"}', False),
        ('{"error":{"type":"master_not_discovered_exception"},"status":503}', False),
    ):
        got = subprocess.run(["grep", "-Eq", pattern.group(1)], input=body, text=True, check=False).returncode == 0
        assert got == ready, body


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


def test_the_provisioning_unit_may_signal_the_manager() -> None:
    """wazuh-passwords-tool.sh restarts the manager and waits for `wazuh-control
    status`, which tests each daemon with `kill -0`. The daemons run as the wazuh
    user and the unit as root, so the unit needs CAP_KILL: without it the tool
    reads them as stopped and exits after it changed two of the three keystores."""
    caps = unit_setting((WAZUH / "cm-wazuh-provision.service").read_text(), "CapabilityBoundingSet")
    assert "CAP_KILL" in caps
    # runuser in indexer-security-init.sh, and the files of three service users.
    assert {"CAP_SETUID", "CAP_SETGID", "CAP_CHOWN", "CAP_FOWNER", "CAP_DAC_OVERRIDE"} <= set(caps)


def test_a_second_run_keeps_the_certificates() -> None:
    """The indexer does not reload certificates while it runs, and `systemctl enable
    --now` does not restart it: a retry that made a new CA would be refused by the
    indexer the first attempt started."""
    script = (WAZUH / "cm-wazuh-provision").read_text()
    make = script.index("wazuh-certs-tool.sh -A")
    guard = script.rindex("if ", 0, make)
    condition = script[guard : script.index("\n", guard)]
    assert "certificates-made" in condition and "passwords_changed" not in condition
    assert script.index('touch "${STATE}/certificates-made"') > make


def test_archives_are_written_and_shipped() -> None:
    """The retention policy and the sizing count on wazuh-archives-*: the manager
    writes archives.json only with logall_json, and Filebeat's template ships
    alerts only."""
    script = (WAZUH / "cm-wazuh-provision").read_text()
    assert "<logall_json>yes</logall_json>" in script
    template = "filebeat.modules:\n  - module: wazuh\n    alerts:\n      enabled: true\n    archives:\n      enabled: false\n"
    command = re.search(r"^sed -i '(/\^    archives:/[^']*)' /etc/filebeat/filebeat.yml$", script, re.MULTILINE)
    assert command is not None
    out = subprocess.run(["sed", command.group(1)], input=template, check=True, capture_output=True, text=True).stdout
    assert out == template.replace("archives:\n      enabled: false", "archives:\n      enabled: true")


MANAGER_SCRIPT: Path = AGENT_SCRIPT.with_name("80-cm-wazuh-manager.sh")
#: The <auth> block of the packaged manager ossec.conf (wazuh-manager 4.14.10-1).
STOCK_MANAGER_CONF: str = """<ossec_config>
  <auth>
    <disabled>no</disabled>
    <port>1515</port>
    <use_source_ip>no</use_source_ip>
    <purge>yes</purge>
    <use_password>no</use_password>
    <ssl_verify_host>no</ssl_verify_host>
  </auth>
</ossec_config>
"""


def run_manager_script(tmp_path: Path) -> str:
    (tmp_path / "etc").mkdir(exist_ok=True)
    conf = tmp_path / "etc" / "ossec.conf"
    if not conf.exists():
        conf.write_text(STOCK_MANAGER_CONF)
    env_file = tmp_path / "manager.env"
    env_file.write_text("WAZUH_REGISTRATION_PASSWORD=secret\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("chgrp", "systemctl"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / name).chmod(0o755)
    subprocess.run(
        ["bash", str(MANAGER_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CM_WAZUH_MANAGER_ENV": str(env_file),
            "CM_OSSEC_DIR": str(tmp_path),
        },
        check=True, capture_output=True, text=True, timeout=30,
    )
    return conf.read_text()


def test_the_manager_lets_a_restarted_agent_enroll_again(tmp_path: Path) -> None:
    """A VM that is restarted alone has a new disk and no key. authd's defaults
    refuse its name for an hour (force: disconnected_time 1h, after_registration_time
    1h); the manager's script turns both waits off, inside <auth>, once."""
    text = run_manager_script(tmp_path)
    auth = ET.fromstring(text).find("auth")
    assert auth is not None
    assert auth.findtext("use_password") == "yes"
    assert auth.findtext("force/enabled") == "yes"
    disconnected = auth.find("force/disconnected_time")
    assert disconnected is not None and disconnected.get("enabled") == "no"
    assert auth.findtext("force/after_registration_time") == "0"
    assert (tmp_path / "etc" / "authd.pass").read_text() == "secret\n"
    assert run_manager_script(tmp_path) == text
    assert text.count("<force>") == 1


HEALTH_STUBS: dict[str, str] = {
    "systemctl": "#!/bin/sh\nexit 0\n",
    "ss": "#!/bin/sh\nfor p in 'tcp 0.0.0.0:1514' 'tcp 0.0.0.0:1515' 'udp 0.0.0.0:514' 'tcp 0.0.0.0:443' 'tcp 0.0.0.0:55000' 'tcp 10.40.10.11:9200'; do echo \"${p% *} LISTEN 0 128 ${p#* } 0.0.0.0:*\"; done\n",
    # The dashboard probe asks for the HTTP status only; the other call is the indexer's health.
    "curl": "#!/bin/sh\ncase \"$*\" in *http_code*) printf '%s' \"${FAKE_DASHBOARD:-302}\"; exit 0 ;; esac\n[ -n \"$FAKE_INDEXER\" ] || exit 7\nprintf '{\"cluster_name\":\"wazuh\",\"status\":\"%s\"}' \"$FAKE_INDEXER\"\n",
    "filebeat": "#!/bin/sh\nexit ${FAKE_FILEBEAT:-0}\n",
    "sed": "#!/bin/sh\necho 10.40.10.11:9200\n",
}


def run_wazuh_health(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in HEALTH_STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    ossec_bin = tmp_path / "ossec" / "bin"
    ossec_bin.mkdir(parents=True, exist_ok=True)
    (ossec_bin / "agent_control").write_text("#!/bin/sh\necho 'ID: 001, Name: ews, IP: any, Active'\n")
    (ossec_bin / "agent_control").chmod(0o755)
    return subprocess.run(
        ["bash", str(WAZUH / "cm-wazuh-health")],
        env={
            **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CM_WAZUH_STATE": str(tmp_path / "state"),
            "CM_OSSEC_DIR": str(tmp_path / "ossec"), "FAKE_INDEXER": "green", **env,
        },
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_wazuh_health_needs_the_whole_pipeline(tmp_path: Path) -> None:
    """Running services and open ports are not enough: the setup must have
    finished, the indexer must answer, and Filebeat must be let in."""
    result = run_wazuh_health(tmp_path)
    assert result.returncode == 1 and result.stdout.startswith("UNHEALTHY: the first-boot setup has not finished")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "provisioned").touch()
    result = run_wazuh_health(tmp_path)
    assert result.returncode == 0 and result.stdout.startswith("HEALTHY: "), result.stdout + result.stderr
    for env, reason in (
        ({"FAKE_INDEXER": ""}, "the indexer does not answer"),
        ({"FAKE_INDEXER": "red"}, "the indexer is not green or yellow"),
        ({"FAKE_FILEBEAT": "1"}, "Filebeat cannot reach the indexer"),
        # A dashboard that cannot sign in to the indexer runs, listens, and answers 503.
        ({"FAKE_DASHBOARD": "503"}, "the dashboard answers HTTP 503"),
    ):
        result = run_wazuh_health(tmp_path, **env)
        assert result.returncode == 1 and result.stdout.startswith(f"UNHEALTHY: {reason}"), result.stdout


def test_soh_does_not_pass_an_unhealthy_siem() -> None:
    """phenix SoH passes a testStdout that is anywhere in the output
    (strings.Contains), and `UNHEALTHY: ...` contains HEALTHY. The health tests
    use a validator that anchors the word."""
    text = (CONFIGS / "scenario-full.yml").read_text(encoding="utf-8")
    assert not re.search(r"testStdout: *HEALTHY", text)
    for script in ("cm-malcolm-health", "cm-wazuh-health"):
        validator = soh_health_test(script)["validateStdout"]
        for output, passes in (("HEALTHY: all up\n", True), ("UNHEALTHY: filebeat is not active\n", False)):
            ran = subprocess.run(["bash", "-c", validator], input=output, capture_output=True, text=True, check=False)
            assert (ran.returncode == 0) == passes, (script, output)


def soh_health_test(script: str) -> dict[str, str]:
    """The SoH custom test of the full scenario that runs ``script``."""
    scenario = yaml.safe_load((CONFIGS / "scenario-full.yml").read_text(encoding="utf-8"))
    found: list[dict[str, str]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if script in str(node.get("testScript", "")):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(scenario)
    assert len(found) == 1, script
    return found[0]


@pytest.mark.parametrize("script", ["cm-malcolm-health", "cm-wazuh-health"])
def test_the_soh_health_test_waits_for_a_slow_start(tmp_path: Path, script: str) -> None:
    """phenix SoH runs a custom test once (only a C2RetryError is retried, and a failed
    expectation is not one), and c2Timeout is how long it waits for the answer. So
    the test waits itself: it asks again until the SIEM is healthy, for less than
    c2Timeout, and prints the last answer either way."""
    test = soh_health_test(script)
    body = test["testScript"].replace(f"/usr/local/sbin/{script}", str(tmp_path / "health"))
    body = body.replace("sleep 15", "sleep 0")
    counter = tmp_path / "count"
    (tmp_path / "health").write_text(
        f'#!/bin/sh\nn=$(cat {counter} 2>/dev/null || echo 0); n=$((n + 1)); echo $n > {counter}\n'
        'if [ "$n" -ge "$HEALTHY_AT" ]; then echo "HEALTHY: up"; else echo "UNHEALTHY: try $n"; exit 1; fi\n'
    )
    (tmp_path / "health").chmod(0o755)
    for healthy_at, tries, passes in ((3, 3, True), (99, 30, False)):
        counter.unlink(missing_ok=True)
        ran = subprocess.run(["bash", "-c", body], env={**os.environ, "HEALTHY_AT": str(healthy_at)}, capture_output=True, text=True, check=False, timeout=60)
        assert int(counter.read_text()) == tries
        ok = subprocess.run(["bash", "-c", test["validateStdout"]], input=ran.stdout, capture_output=True, text=True, check=False)
        assert (ok.returncode == 0) == passes, ran.stdout
    # 30 tries 15 s apart stay inside the 15 minutes SoH waits for the answer.
    scenario = yaml.safe_load((CONFIGS / "scenario-full.yml").read_text(encoding="utf-8"))
    soh = next(app for app in scenario["spec"]["apps"] if app["name"] == "soh")
    assert soh["metadata"]["c2Timeout"] == "15m" and "seq 1 30" in test["testScript"] and "sleep 15" in test["testScript"]


def profile_for(tmp_path: Path, pages: str, page_size: str = "4096", profile: str = "auto") -> subprocess.CompletedProcess[str]:
    """cm_malcolm_profile with a stand-in getconf (the memory of the VM)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "getconf").write_text(
        f'#!/bin/sh\ncase "$1" in _PHYS_PAGES) echo "{pages}" ;; PAGE_SIZE) echo "{page_size}" ;; *) exit 1 ;; esac\n'
        if pages != "missing" else "#!/bin/sh\nexit 1\n"
    )
    (bin_dir / "getconf").chmod(0o755)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", f'. "{MALCOLM / "profiles.sh"}"; profile=$(cm_malcolm_profile); echo "$profile"'],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CM_MALCOLM_PROFILE": profile},
        capture_output=True, text=True, check=False,
    )


def test_the_malcolm_vm_of_the_topology_gets_the_full_profile(tmp_path: Path) -> None:
    """The profile follows the VM's memory, read without /proc/meminfo: the
    provisioning unit hides it (ProcSubset=pid), and the read that failed there
    made every VM slim."""
    soc = yaml.safe_load((CONFIGS / "topology-soc.yml").read_text(encoding="utf-8"))
    malcolm = next(n for n in soc["spec"]["nodes"] if n["general"]["hostname"] == "malcolm")
    # What Linux reports of the VM's memory: all of it less the kernel's own, about 2 %.
    pages = int(malcolm["hardware"]["memory"] * 1024 * 1024 * 0.98) // 4096
    assert profile_for(tmp_path, str(pages)).stdout == "full\n"
    assert profile_for(tmp_path, str(12 * 1024 * 1024 * 1024 // 4096)).stdout == "slim\n"
    assert profile_for(tmp_path, "100", profile="full").stdout == "full\n"
    for unreadable in ("missing", "", "many"):
        failed = profile_for(tmp_path, unreadable)
        assert failed.returncode != 0 and failed.stdout == "" and "cannot read the memory size" in failed.stderr, unreadable
    assert "/proc/meminfo)" not in (MALCOLM / "profiles.sh").read_text().split("cm_malcolm_profile() {")[1]


def test_no_sandboxed_script_reads_the_parts_of_proc_its_unit_hides() -> None:
    """ProcSubset=pid leaves only the process directories in /proc: no meminfo,
    cpuinfo, loadavg, uptime, or sys. The range's own scripts must not read them
    (free reads /proc/meminfo too); 14 of its units are sandboxed that way."""
    units = [p for p in INJECTS.rglob("*.service") if "ProcSubset=pid" in p.read_text()]
    assert len(units) >= 10
    hidden = re.compile(r"/proc/(?!self\b|thread-self\b|\$|[0-9])[a-z]|\bfree +-|\buptime\b|\bnproc\b")
    for path in INJECTS.rglob("*"):
        if not path.is_file() or path.suffix in (".png", ".zip", ".pyc", ".xml", ".in", ".txt", ".rules", ".zeek", ".md"):
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            code = line.split("#", 1)[0]
            assert not hidden.search(code), f"{path.relative_to(INJECTS)}:{number}: {line.strip()}"


def test_the_full_profile_extracts_files_for_the_scanners() -> None:
    """Malcolm extracts no file unless told: without it, Strelka and the scanner of the
    full profile never get one. Analysts fetch what is kept as password-protected ZIPs."""
    full = malcolm_settings("full")
    assert (full["fileCarveMode"], full["filePreserveMode"], full["extractedFileMaxSizeThreshold"]) == ("interesting", "quarantined", "2G")
    assert (full["fileCarveHttpServer"], full["fileCarveHttpServerZip"], full["fileCarveHttpServeEncryptKey"]) == (True, True, "infected")
    assert malcolm_settings("slim")["fileCarveMode"] == "none"


SELFTEST_LOGTEST: str = """#!/bin/sh
# Like wazuh_logtest.py: the report is logging.info on stderr, and -q hides it.
read -r line
for arg in "$@"; do [ "$arg" = -q ] && exit 0; done
printf "**Phase 3: Completed filtering (rules).\\n\\tid: '%s'\\n" "$FAKE_RULE" >&2
"""


def test_the_selftest_reads_the_report_of_logtest(tmp_path: Path) -> None:
    """wazuh-logtest -q prints nothing, so the selftest must not use it."""
    ossec_bin = tmp_path / "bin"
    ossec_bin.mkdir()
    (ossec_bin / "wazuh-logtest").write_text(SELFTEST_LOGTEST)
    (ossec_bin / "wazuh-logtest").chmod(0o755)
    samples = tmp_path / "samples.txt"
    samples.write_text("# a comment\n100101,100102|Nov  9 12:00:00 fw-ot kernel: [  123.456789] x\n")
    for rule, code, word in (("100102", 0, "PASS"), ("100103", 1, "FAIL")):
        result = subprocess.run(
            ["bash", str(WAZUH / "cm-wazuh-selftest")],
            env={**os.environ, "CM_WAZUH_SAMPLES": str(samples), "CM_OSSEC_DIR": str(tmp_path), "FAKE_RULE": rule},
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == code and result.stdout.startswith(word), result.stdout


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


def test_the_agent_reads_each_log_once(tmp_path: Path) -> None:
    """The installed agent reads the journal, and noble's journald forwards the same
    lines to the files the groups' agent.conf reads: every sign-in and firewall drop
    would reach the manager twice, and a rule that counts would fire at half the
    count. The hook removes the journald reader and nothing else."""
    assert "<log_format>journald</log_format>" in STOCK_AGENT_CONF
    root = ET.fromstring(run_agent_script(tmp_path, "ops"))
    assert [lf.findtext("location") for lf in root.findall("localfile")] == ["/var/ossec/logs/active-responses.log"]
    for group in ("ops", "dmz", "hmi", "bas", "soc"):
        shared = (WAZUH / "agent-conf" / f"{group}.conf").read_text()
        assert "journald" not in shared.split("-->")[-1] and "/var/log/auth.log" in shared


def test_the_wazuh_setup_may_make_the_system_calls_its_tools_need() -> None:
    """`~@resources` takes setpriority out of the filter. PAM's pam_limits calls it in
    every runuser, which Wazuh's indexer-security-init.sh uses, and the kernel kills
    the caller: the first-boot setup stopped at step 2. @pkey is for the dashboard's
    keystore tool (Node 18): killed there, the password tool goes on and leaves the
    dashboard with a password the indexer no longer takes."""
    filters = unit_setting((WAZUH / "cm-wazuh-provision.service").read_text(), "SystemCallFilter")
    assert filters == ["@system-service", "@pkey"]
    script = (WAZUH / "cm-wazuh-provision").read_text()
    # The waits see a dashboard that is up and cannot sign in (503).
    assert "curl -sk -o /dev/null https://localhost:443/" not in script
    assert script.count('wait_for 600 "the dashboard" dashboard_answers') == 2
    # The retention policies exist before Filebeat makes the first index.
    assert script.index("retention alerts 'wazuh-alerts-*'") < script.index("systemctl enable --now filebeat.service")


# --- the vendor-maintenance run's expectations ------------------------------------------

#: `docker exec ... curl -d @-`: keep the query, answer a count.
EXPECT_DOCKER: str = """#!/bin/sh
cat > "$FAKE_QUERY"
printf '{"count": 3}\\n'
"""


def scorch_args(script: str) -> list[str]:
    """The arguments of the SCORCH cc step of the full scenario that runs ``script``."""
    scenario = yaml.safe_load((CONFIGS / "scenario-full.yml").read_text(encoding="utf-8"))
    scorch = next(app for app in scenario["spec"]["apps"] if app["name"] == "scorch")
    for component in scorch["metadata"]["components"]:
        for vm in (component.get("metadata") or {}).get("vms") or []:
            for stage in ("configure", "start", "stop", "cleanup"):
                for step in vm.get(stage) or []:
                    if script in str(step.get("args", "")):
                        return shlex.split(step["args"])
    raise AssertionError(f"no SCORCH step runs {script}")


def expect_phrases() -> list[str]:
    args = scorch_args("cm-malcolm-expect")[1:]
    if args[0] == "--since":
        args = args[2:]
    return args


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_malcolm_expect_matches_the_start_of_a_signature(tmp_path: Path) -> None:
    """Malcolm maps rule.name (the Suricata signature) as a keyword (ECS): a quoted
    phrase matches it only as the whole value, so the start must match too."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(EXPECT_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    proc = subprocess.run(
        ["bash", str(MALCOLM / "cm-malcolm-expect"), "--since", "45", 'CM "WireGuard" handshake'],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_QUERY": str(tmp_path / "query.json")},
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith('PASS: "CM "WireGuard" handshake" found 3 time(s) in the last 45 minutes')
    query = json.loads((tmp_path / "query.json").read_text())["query"]["bool"]
    assert query["filter"] == [{"range": {"@timestamp": {"gte": "now-45m"}}}]
    assert query["minimum_should_match"] == 1
    assert {"prefix": {"rule.name": 'CM "WireGuard" handshake'}} in query["should"]
    assert {"prefix": {"zeek.notice.note": 'CM "WireGuard" handshake'}} in query["should"]
    assert {"query_string": {"query": '"CM \\"WireGuard\\" handshake"'}} in query["should"]


def test_the_run_expects_detections_that_exist() -> None:
    """Each phrase of the expect-malcolm step starts a Suricata signature of
    cherrymountain.rules or names a notice of cherrymountain.zeek."""
    signatures = re.findall(r'msg:"([^"]+)"', (MALCOLM / "cherrymountain.rules").read_text())
    zeek = (MALCOLM / "zeek" / "cherrymountain.zeek").read_text()
    module = re.search(r"^module (\w+);", zeek, re.MULTILINE)
    assert module is not None
    phrases = expect_phrases()
    assert phrases, "the expect-malcolm step names no detection"
    for phrase in phrases:
        if phrase.startswith(f"{module.group(1)}::"):
            note = phrase.split("::", 1)[1]
            assert re.search(rf"\$note={note}\b", zeek), f"no notice {phrase} in cherrymountain.zeek"
        else:
            assert any(s.startswith(phrase) for s in signatures), f"no Suricata signature starts with {phrase!r}"


def test_the_run_expects_wazuh_rules_that_exist() -> None:
    rules = {int(i) for i in re.findall(r'<rule id="(\d+)"', (WAZUH / "cherrymountain_rules.xml").read_text())}
    groups = [a for a in scorch_args("cm-wazuh-expect")[1:] if re.fullmatch(r"\d{6}(,\d{6})*", a)]
    assert groups, "the expect-wazuh step names no rule"
    for group in groups:
        missing = {int(i) for i in group.split(",")} - rules
        assert not missing, f"expect-wazuh names rules that do not exist: {sorted(missing)}"
