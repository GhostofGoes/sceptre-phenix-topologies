# phenix images

One phenix `Image` config per VM role of the CherryMountain Brewery topology.
This file has one section per image, in a fixed order: purpose, base,
contents, build, used by, and pinned versions. Add a section here in the same
change that adds an image config.

Each file is the stored form of an image (what `phenix config get image/<name>`
prints). The `POSTBUILD_*` scripts in them are verbatim copies of the defaults
that `phenix image create` adds (`src/go/api/image/constants.go`), so the lint
hooks skip them. Only the `INSTALL_*` script of each image is ours.

Every image is Ubuntu 24.04 (noble), qcow2, built from the
`us.archive.ubuntu.com` mirror with the `main restricted universe multiverse`
components. All are `minbase` except `cm-desktop` and `cm-ews`, which are
`mingui` (xubuntu, with the phenix GUI autologin). They share this package set: `ca-certificates chrony curl dbus
ethtool init initramfs-tools iproute2 iputils-ping isc-dhcp-client less
linux-headers-generic linux-image-generic ncat net-tools netbase nftables
openssh-server procps rsync ssh tcpdump tmux vim wget`. The sections list only
what each image adds. The base is not pinned beyond the release name: apt
versions move with noble's updates.

Build any image from the repository root, on a host with phenix and
`/dev/kvm`. Replace `<name>` with the image name:

```shell
phenix config create cherrymountain-brewery/phenix-images/<name>.yml
phenix image build -o <dir> -c -x <name>
phenix image inject-miniexe /usr/local/bin/miniccc <dir>/<name>.qc2
```

`inject-miniexe` adds `miniccc`, which SoH, cc, and SCORCH need (DESIGN.md
15.7). The topology refers to the result as `<name>.qc2`.

The lint hooks (see `prek.toml`) check these files against the sceptre-phenix
`Image` schema, run shellcheck on every `INSTALL_*` script, and run
`systemd-analyze verify` plus an exposure limit on the units those scripts
write.

## cm-sim

- **Purpose:** the HELICS broker and the process federates.
- **Base:** the shared noble `minbase` image, 8 GB disk.
- **Contents:**
  - `python3`, `python3-venv`, and `python3-pip`.
  - A venv at `/opt/cherrymountain/venv` with the `helics` wheel (which also
    ships `helics_broker`, `helics_app`, `helics_player`, and
    `helics_recorder`, linked into `/usr/local/bin`), `pydantic`, and the
    `cherrymountain-sim` package from this repository's `sim/` directory.
  - `/etc/cherrymountain`. The `cm-cellar.service` unit and its config are
    injected by the topology, not baked in.
- **Build:** the commands above, with `<name>` set to `cm-sim`.
- **Used by:** `sim-ctl` (HELICS root broker) and `sim-process` (cellar
  federate) in the ci variant.
- **Pinned versions:** `helics==3.6.1`, `pydantic>=2.7` (a floor, not a pin).
  `cherrymountain-sim` comes from the `main` branch archive (`CM_REF=main`).
  That is not pinned yet: replace it with a release tag or commit.

## cm-otsim

- **Purpose:** ot-sim field devices: the remote I/O servers (`fd-server`) that
  speak Modbus to the PLCs and HELICS to the simulation.
- **Base:** the shared noble `minbase` image plus the ot-sim build
  dependencies, 10 GB disk. The build dependencies are purged at the end.
- **Contents:**
  - ot-sim (the C message bus, the Go cpu, modbus, and logic modules, and the
    Python modules) built from source and installed under `/usr/local`.
  - A venv at `/opt/otsim-venv` with `helics` and the ot-sim Python package.
  - `ot-sim.service`, which starts the CPU module after `phenix.service` and
    stays inactive until the ot-sim app injects `/etc/ot-sim/config.xml`.
- **Build:** the commands above, with `<name>` set to `cm-otsim`.
- **Used by:** `rio-cellar` in the ci variant.
- **Pinned versions:** `helics==3.6.1`. ot-sim is built from `OTSIM_REF=main`,
  which is not pinned yet. The integration test image
  (`tests/integration/Dockerfile.otsim`) builds commit
  `9dc33047ff9fb474e61c8931c7dac7bf98df38ef`; use it here once the noble spike
  confirms it.

## cm-openplc

- **Purpose:** the OpenPLC v4 runtime that runs the cellar PLC program.
- **Base:** the shared noble `minbase` image plus `build-essential`, `python3`,
  `python3-pip`, and `python3-venv`, 12 GB disk. The runtime compiles uploaded
  programs on the VM, so the toolchain stays.
- **Contents:**
  - `openplc-runtime` cloned to `/opt/openplc-runtime` and installed with
    `install.sh --native`.
  - `openplc-runtime.service`, the unit that `install.sh` writes when systemd
    is running (it does not in the image-build chroot).
  - `git`, `zip`, and `unzip` for the provisioning script.
  - `/var/lib/cm-openplc`, `/opt/cherrymountain/openplc`, and
    `/var/lib/openplc-runtime`. The program ZIP, the plugin configs, and
    `cm-openplc-provision` are injected by the topology.
- **Build:** the commands above, with `<name>` set to `cm-openplc`. The
  program ZIP is built separately (`plc/plc-cellar/build_zip.sh`).
- **Used by:** `plc-cellar` in the ci variant.
- **Pinned versions:** `OPENPLC_REF=v4.3.2` (a tag of
  `Autonomy-Logic/openplc-runtime`). Whether `install.sh --native` works in the
  build chroot is marked `TODO(verify)`; `spikes/openplc-v4` runs the same
  install on a live VM.

## cm-fuxa

- **Purpose:** the FUXA SCADA server and HMI.
- **Base:** the shared noble `minbase` image plus `git`, `xz-utils`,
  `python3`, `build-essential`, and `libsqlite3-dev` for the build, 12 GB disk.
- **Contents:**
  - Node.js from the nodejs.org tarball, unpacked under `/usr/local`.
  - FUXA at `/opt/fuxa` (one commit, built with `npm run build`, dev
    dependencies pruned) with the driver plugins installed so the range runs
    offline.
  - `fuxa.service`, which runs `node /opt/fuxa/server/main.js`.
  - `/etc/cherrymountain/fuxa`. The project JSON, `mysettings.json`, and
    `cm-fuxa-provision` are injected by the topology.
  - The Wazuh agent, disabled ([Wazuh agent](#wazuh-agent)).
- **Build:** the commands above, with `<name>` set to `cm-fuxa`.
- **Used by:** `scada-fuxa` in the ci variant.
- **Pinned versions:** Node.js `22.14.0`; FUXA commit
  `b2972583b3d59525131c44e8b3214f55b74750fe` (server 1.3.6); plugins
  `modbus-serial@8.0.19`, `node-opcua@2.149.0`, `node-snap7@1.0.9`, and
  `node-bacnet@0.2.4`. Other npm dependencies follow FUXA's lockfile.

## cm-svc

- **Purpose:** the small network services: ot-svc (NTP, DNS, syslog relay, and
  the OPS BBMD), bas-bbmd (the BAS BBMD), and inet-nuke (the internet-side
  name and time service, and the protonuke server).
- **Base:** the shared noble `minbase` image plus `dnsmasq`, `rsyslog`,
  `python3`, and `python3-venv`, 8 GB disk.
- **Contents:**
  - A venv at `/opt/bacnet` with BACpypes3 and `pydantic`, which the
    `cm-bbmd.service` unit runs. The unit, `cm_bbmd.py`, and its settings are
    injected by the topology (`phenix-injects/bbmd/`), and the startup hook
    enables the unit on ot-svc and bas-bbmd only.
  - `dnsmasq` is installed and disabled. The injected plant zone
    (`phenix-injects/ot-svc/`, `phenix-injects/inet-nuke/`) and the startup
    hooks configure and start it.
  - `/etc/cherrymountain` and `/usr/local/lib/cm-bbmd`.
  - The Wazuh agent, disabled ([Wazuh agent](#wazuh-agent)).
- **Build:** the commands above, with `<name>` set to `cm-svc`. inet-nuke
  uses a copy with protonuke added: copy `cm-svc.qc2` to
  `cm-svc-protonuke.qc2`, then run `phenix image inject-miniexe
  /usr/local/bin/protonuke <dir>/cm-svc-protonuke.qc2`.
- **Used by:** `ot-svc`, `bas-bbmd`, and (as `cm-svc-protonuke.qc2`)
  `inet-nuke`.
- **Pinned versions:** `bacpypes3==0.0.110` (the version of the BBMD spike),
  `pydantic>=2.7` (a floor, not a pin), and the Wazuh agent (see
  [Wazuh agent](#wazuh-agent)).

## Wazuh agent

`cm-svc`, `cm-fuxa`, `cm-wireguard`, `cm-guacamole`, `cm-malcolm`, `cm-desktop`,
`cm-ews`, and `cm-historian` carry the Wazuh agent (DESIGN.md 14, which lists the
Linux hosts in OPS, HMI, BAS, the DMZ, and the SOC). All of them install it the
same way, and none enables it:

- The APT key is fetched and checked against Wazuh's published fingerprint
  (`0DCFCA5547B19D2A6099506096B3EE5F29111145`), and the repository is a deb822
  source with `Signed-By`.
- The package is pinned to `4.14.10-1`, the version of the `cm-wazuh` manager. An
  agent must not be newer than its manager, so change the two together.
- The cherrymountain app's `80-cm-wazuh-agent.sh` points the agent at the manager
  and enrolls it (password enrollment, group = the node's `zone` label) at boot,
  and enables the service. Until then the service is disabled.

The PLCs, the ot-sim devices, the simulation nodes, and the routers have no agent
(DESIGN.md 14).

## cm-wireguard

- **Purpose:** the WireGuard gateway of the DMZ (`dmz-vpn`), the DNAT target of
  fw-edge for the vendor tunnel (DESIGN.md 13).
- **Base:** the shared noble `minbase` image plus `wireguard-tools`, `gnupg`,
  `python3`, `python3-pydantic`, and `rsyslog`, 8 GB disk. It is the focal
  `renewables/wind/plant/phenix-images/wireguard.yml` rebased on noble.
- **Contents:**
  - `wireguard-tools` (the kernel has WireGuard).
  - `net.ipv4.ip_forward=1` in `/etc/sysctl.d`, because dmz-vpn forwards the
    decapsulated tunnel traffic onto the DMZ without NAT.
  - `/etc/wireguard` (mode 0700) and `/usr/local/lib/cm-wireguard`.
  - The Wazuh agent, disabled.
  - The topology injects the rest from `phenix-injects/wireguard/`: the nftables
    policy that pins the vendor peer to dmz-jump on TCP 443, `cm_wg_watch.py`
    with its unit (it writes a line for every tunnel handshake, new endpoint, and
    timeout, which Wazuh reads), and the startup hook. The phenix `wireguard`
    app injects `wg0.conf` and enables `wg-quick@wg0`.
- **Build:** the commands above, with `<name>` set to `cm-wireguard`.
- **Used by:** `dmz-vpn`.
- **Pinned versions:** the Wazuh agent `4.14.10-1`. `wireguard-tools` and
  `python3-pydantic` follow noble.

## cm-guacamole

- **Purpose:** the Guacamole jump host (`dmz-jump`): the one conduit from the DMZ
  into the EWS for vendor sessions, with TOTP and session recording (DESIGN.md 13).
- **Base:** the shared noble `minbase` image plus Docker, `skopeo`, `openssl`,
  `gnupg`, `python3`, `python3-pydantic`, and `rsyslog`, 16 GB disk.
- **Contents:**
  - Docker Engine and the Compose plugin from Docker's APT repository.
  - Four container images as compressed on-disk copies under
    `/var/lib/cm-guacamole/images`, listed in `/etc/cherrymountain/images.list`.
    A build has no Docker daemon, so `cm-load-images.service` (injected, from
    `phenix-injects/docker/`) loads them into Docker at the first boot and deletes
    the copies.
  - The stock PostgreSQL schema (`001-create-schema.sql`, checked against the
    Apache checksum) in `/opt/cherrymountain/guacamole/initdb`. The stock
    `guacadmin` account (`002-create-admin-user.sql`) is deliberately left out;
    the cherrymountain app's `50-cherrymountain.sql` creates `cmadmin`, and the
    topology's `60-ridgeline.sql` creates the vendor account with TOTP.
  - A recordings directory (`/var/lib/cm-guacamole/recordings`, mode 2750,
    owner uid 1000, group 1001) that guacd writes, the web app reads, and the
    Wazuh agent watches.
  - The Wazuh agent, disabled.
  - The topology injects the Compose file, the nginx TLS front end, the units,
    and the settings from `phenix-injects/guacamole/`. TOTP and the recording
    storage extension are inside the pinned `guacamole/guacamole` image, and are
    switched on by environment variables.
- **Build:** the commands above, with `<name>` set to `cm-guacamole`. The build
  needs internet for Docker Hub, Docker's APT repository, and archive.apache.org.
- **Used by:** `dmz-jump`.
- **Pinned versions:** Docker `5:29.9.3-1~ubuntu.24.04~noble`, Compose plugin
  `5.7.0-1~ubuntu.24.04~noble`, `guacamole/guacd:1.6.0`,
  `guacamole/guacamole:1.6.0`, `postgres:17.11-alpine`, `nginx:1.30.6-alpine`,
  and the Guacamole JDBC schema from `guacamole-auth-jdbc-1.6.0.tar.gz`
  (SHA-256 `97bc5fd3...a7039366`). The images are tags, not digests.

## cm-malcolm

- **Purpose:** the Malcolm network sensor (`malcolm`): Zeek with the ICSNPP
  parsers, Suricata, Arkime, and OpenSearch, capturing live from the mirror app's
  feed (DESIGN.md 14). The largest image.
- **Base:** the shared noble `minbase` image plus Docker, Open vSwitch, `git`,
  `skopeo`, `jq`, `openssl`, `apache2-utils`, `python3-pydantic`, and
  `python3-venv`, 64 GB disk.
- **Contents:**
  - Docker Engine and the Compose plugin from Docker's APT repository.
  - Malcolm's own checkout at `/opt/malcolm` (the commit of the `v26.10.0` tag,
    checked, without `.git`), owned by uid 1000 (the user `malcolm`), and a venv
    `/opt/malcolm-venv` with the Python modules Malcolm's `auth_setup` needs.
  - The 21 Malcolm images of the full profile as compressed on-disk copies under
    `/var/lib/cm-malcolm/images`, about 10 GB, loaded into Docker at the first
    boot by `cm-load-images.service`. One image serves both sizing profiles: the
    full profile (8 vCPU / 24 GB) and the slim profile (4 vCPU / 12 GB) differ only
    in the services that start and the heap sizes (`phenix-injects/malcolm/profiles.sh`;
    `CM_MALCOLM_PROFILE=auto` picks by memory).
  - Open vSwitch, enabled, for the mirror app's `setupOVS`.
  - Sysctl and limits for OpenSearch (`vm.max_map_count` and others).
  - The Wazuh agent, disabled.
  - The topology injects the sensor from `phenix-injects/malcolm/`. The first boot
    (`cm-malcolm-provision`) bakes in the four fixes of the malcolm spike: it
    creates `zeek/custom/__load__.zeek` (the bind mount hides the image's),
    sets `PCAP_PIPELINE_VERBOSITY` in `config/upload-common.env`, and refuses
    custom Suricata rules with a sid below 9000101; `cm-malcolm-wait-ready` waits
    for the processors' ZeroMQ subscriptions before the first pcap. The capture
    unit adds the OpenFlow flow (`in_port=sniffer,actions=output:phenix`) and
    brings the `phenix` port up (DESIGN.md 14).
- **Build:** the commands above, with `<name>` set to `cm-malcolm`. The build
  downloads about 10 GB from `ghcr.io` and needs the 64 GB disk.
- **Used by:** `malcolm`.
- **Pinned versions:** Malcolm `v26.10.0` (commit
  `36bb3a61217b8555bb9fe8918acd3b45927a5458`), its images at `26.10.0`, Docker
  `5:29.9.3-1~ubuntu.24.04~noble`, Compose plugin `5.7.0-1~ubuntu.24.04~noble`, and
  in the venv `kubernetes==37.0.2`, `python-dotenv==1.2.4`, `pyyaml==6.0.3`,
  `requests==2.34.2`, and `ruamel.yaml==0.19.1`.

## cm-wazuh

- **Purpose:** the SIEM (`wazuh`): the Wazuh manager, indexer, and dashboard on
  one VM (DESIGN.md 14).
- **Base:** the shared noble `minbase` image plus `gnupg`, `jq`, `openssl`,
  `python3`, and `rsyslog`, 16 GB disk.
- **Contents:**
  - `wazuh-manager`, `wazuh-indexer`, `wazuh-dashboard`, and Wazuh's Filebeat
    (7.10.2, the build that pairs with the indexer), installed from the packages
    with all four services disabled. Everything that depends on the VM's address,
    the certificates, and the passwords happens at the first boot, in
    `cm-wazuh-provision.service` (`phenix-injects/wazuh/`), so the range needs no
    internet.
  - Wazuh's certificate tool, password tool, Filebeat template, and Filebeat
    module in `/opt/cherrymountain/wazuh`, checked against SHA-256 values.
  - An indexer heap of 2 GB (`/etc/wazuh-indexer/jvm.options.d/cm-heap.options`)
    and `vm.max_map_count`.
  - The topology injects the rules, decoders, agent group settings, the syslog
    listener, and the provisioning unit. The cherrymountain app's
    `80-cm-wazuh-manager.sh` turns on password enrollment.
- **Build:** the commands above, with `<name>` set to `cm-wazuh`.
- **Used by:** `wazuh`.
- **Pinned versions:** `wazuh-manager`, `wazuh-indexer`, `wazuh-dashboard`
  `4.14.10-1`, Filebeat `7.10.2-2`, and the helper files of the 4.14 release by
  SHA-256 (`wazuh-certs-tool.sh`, `wazuh-passwords-tool.sh`, `filebeat.yml`,
  `wazuh-template.json`, `wazuh-filebeat-0.5.tar.gz`).

## cm-desktop

- **Purpose:** the workstation desktop for every role that has one: the HMI
  kiosks (`hmi-brewhouse`, `hmi-packaging`), `corp-ws`, `vendor-ws`, and
  `soc-analyst` (DESIGN.md 11).
- **Base:** the phenix `mingui` variant (xubuntu, root autologin) on noble, plus
  `wireguard-tools`, `python3-pydantic`, `gnupg`, and `rsyslog`, 24 GB disk.
- **Contents:**
  - Chromium 150 from Debian 13 (trixie). Ubuntu's `chromium-browser` is a snap
    shim that cannot be installed offline, so the Debian repository is added at
    pin priority 100 (only `chromium`, `chromium-sandbox`, and the libraries they
    need come from it), and chromium is held to major version 150. A flags file
    in `/etc/chromium.d` adds `--no-sandbox` (the desktop runs as root) and
    `--disable-gpu`.
  - `wireguard-tools`, for `vendor-ws` (the phenix `wireguard` app injects
    `wg0.conf` and enables the tunnel).
  - Xfce settings that turn off the screen saver, the lock screen, and display
    power management, because the root session has no password to unlock.
  - The Wazuh agent, disabled.
  - The roles differ only in what is injected: the app's kiosk script, desktop
    entry, and URL (HMIs); protonuke (`corp-ws` and `vendor-ws`); the scripted
    vendor session (`vendor-ws`, `phenix-injects/desktop/`); and the console
    launcher (`soc-analyst`).
- **Build:** the commands above, with `<name>` set to `cm-desktop`, and then
  `phenix image inject-miniexe /usr/local/bin/protonuke <dir>/cm-desktop.qc2`
  for the protonuke app.
- **Used by:** `hmi-brewhouse`, `hmi-packaging`, `corp-ws`, `vendor-ws`,
  `soc-analyst`.
- **Pinned versions:** Chromium `150.*` (Debian trixie), the Wazuh agent
  `4.14.10-1`. The Debian archive key is checked against fingerprint
  `04B54C3CDCA79751B16BC6B5225629DF75B188BD`.

## cm-ews

- **Purpose:** the engineering workstation (`ews`), and the one choke point from
  the DMZ into L1 (DESIGN.md 12.4 and 13).
- **Base:** the phenix `mingui` variant on noble, plus `xrdp`, `xorgxrdp`,
  `wireshark`, `tshark`, `mbpoll`, `polkitd`, `python3-venv`, `zip`, `unzip`, and
  `gnupg`, 28 GB disk.
- **Contents:**
  - OpenPLC Editor 4.3.4, the Linux AppImage unpacked to `/opt/openplc-editor`
    (no FUSE) with a launcher that passes `--no-sandbox`.
  - Chromium 150 from Debian 13, the same way as in `cm-desktop`, for the FUXA
    editor and the OpenPLC REST UI.
  - Wireshark and `mbpoll`, and a venv `/opt/ews-venv` with BAC0, pymodbus, and
    asyncua (whose command line tools `uals`, `uaread`, `uawrite`, and `uacall`
    stand in for an OPC UA client).
  - xrdp, enabled, with a polkit rule for the color-manager prompt and
    `xfce4-session` in `/etc/skel/.xsession`, so an RDP login lands in Xfce.
  - The Wazuh agent, disabled.
  - The topology injects the vendor account (`phenix-injects/ews/`).
- **Build:** the commands above, with `<name>` set to `cm-ews`.
- **Used by:** `ews`.
- **Pinned versions:** OpenPLC Editor `4.3.4` (SHA-256 `b8186372...ec75fa0`),
  `BAC0==2026.7.25`, `pymodbus==3.16.2`, `asyncua==2.1.0`, Chromium `150.*`, the
  Wazuh agent `4.14.10-1`. `opcua-client` is not used: its current release needs
  Python 3.14, and noble has 3.12.

## cm-historian

- **Purpose:** the plant historian (`historian`): InfluxDB 2, Telegraf, Grafana,
  and Mosquitto (DESIGN.md 12.4).
- **Base:** the shared noble `minbase` image plus `mosquitto`,
  `mosquitto-clients`, `gnupg`, `jq`, `python3`, and `rsyslog`, 12 GB disk.
- **Contents:**
  - InfluxDB 2 OSS and its CLI (`influxdb2`, `influxdb2-cli`), Telegraf, and
    Grafana from their vendors' APT repositories (keys checked against their
    fingerprints), all enabled.
  - A Grafana drop-in with anonymous Viewer access (the HMI kiosks read it
    without a login, DESIGN.md 10.3), the plant dashboard as the home page, and
    no plugin downloads, update checks, or news feed (the VMs have no internet).
  - The Wazuh agent, disabled.
  - The cherrymountain app renders the rest at boot: credentials, `telegraf.conf`,
    the Mosquitto listeners, the Grafana data sources and dashboard, and the
    InfluxDB provisioning script (organization, buckets, token).
- **Build:** the commands above, with `<name>` set to `cm-historian`.
- **Used by:** `historian`.
- **Pinned versions:** `influxdb2` and `influxdb2-cli` `2.9.1-1`, Telegraf
  `1.40.3-1`, Grafana `12.*` (the vendor's major version), the Wazuh agent
  `4.14.10-1`. Mosquitto follows noble (2.0.x).

## Apps

phenix apps are not in this repository yet. When the `cherrymountain` app (and
any other app) is added under `apps/`, it ships its own `README.md` next to its
code, covering its metadata, stages, and tests. See the top-level
[README](../README.md).
