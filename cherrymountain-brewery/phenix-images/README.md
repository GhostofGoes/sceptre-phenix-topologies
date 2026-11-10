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
`mingui` (xubuntu, with the phenix GUI autologin). `phenix config create`
stores a config as it is, and only `phenix image create` adds a variant's
packages, so those two files list `xubuntu-desktop`, `wmctrl`, and `xdotool`
themselves. They share this package set: `ca-certificates chrony curl dbus
ethtool init initramfs-tools iproute2 iputils-ping isc-dhcp-client less
linux-headers-generic linux-image-generic ncat net-tools netbase nftables
openssh-server procps rsync ssh tcpdump tmux vim wget`. The sections list only
what each image adds. The base is not pinned beyond the release name: apt
versions move with noble's updates.

Build any image from the repository root, in the phenix container (it has
vmdb2 and debootstrap; the build itself needs internet access and no
`/dev/kvm`, and `inject-miniexe` needs minimega running). The result is
`<dir>/<name>.qc2`. Replace `<name>` with the image name:

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

How phenix runs the scripts, which every `INSTALL_*` script must allow for:

- phenix joins the scripts of `script_order` into one script and drops its
  blank lines. vmdb2 runs it with `sh -ec` in the image's chroot, and `sh` is
  dash on Ubuntu: no bash syntax, one failed command ends the build, and a
  variable or a `cd` of one script carries into the next.
- vmdb2 renders the script with Jinja2 first, so `{{`, `{%`, and `{#` (as in
  `${#name}`) must not appear.
- The chroot has `/proc`, `/sys`, `/dev`, and the build host's `/run`, and the
  host's `resolv.conf`. No systemd runs in it: `systemctl enable` and
  `disable` work, and `start` does nothing.
- With `-c`, vmdb2 keeps the debootstrapped root with the `packages` list
  installed in `<dir>/<name>.tar` and starts the next build from it. Delete
  that file after a change to `packages`.

The image lint hook checks the first two points and the `mingui` packages.
Build in the phenix container: `xubuntu-desktop` recommends Ubuntu's `firefox`
package, which installs a snap through the host's snapd when the build host
has one (`/run/snapd.socket`), and skips it when it has none.

## cm-sim

- **Purpose:** the simulation nodes: the HELICS root broker, the
  cherrymountain_sim federates, and on sim-ctl the ground truth (DESIGN.md
  9.5).
- **Base:** the shared noble `minbase` image plus `gnupg`, `jq`, `python3`,
  `python3-venv`, and `python3-pip`, 16 GB disk (the OpenSearch package alone
  is about 1 GB).
- **Contents:**
  - A venv at `/opt/cherrymountain/venv` with the `helics` wheel (which also
    ships `helics_broker`, `helics_app`, `helics_player`, and
    `helics_recorder`, linked into `/usr/local/bin`) and the
    `cherrymountain-sim[power]` package from this repository's `sim/`
    directory at `CM_REF`, which brings `pydantic`, SimPy, and
    OpenDSSDirect.py. SciPy, PackMLpy, and `ht` are test-only and not
    installed.
  - OpenSearch (single node, `0.0.0.0:9200`, security plugin disabled, 2 GB
    heap) for the ground-truth indexes `cherrymountain-truth-*`.
  - Grafana with the `grafana-opensearch-datasource` plugin, a provisioned
    "Ground truth" data source on `127.0.0.1:9200`, and a starter dashboard.
    It serves an anonymous read-only view on port 3000; the admin password
    comes from `ground-truth.env`.
  - Both units carry a drop-in with
    `ConditionPathExists=/etc/cherrymountain/ground-truth.env`. The
    `cherrymountain` app injects that file on sim-ctl in the full and lite
    variants only, so the other sim nodes and the ci variant never start them.
  - `/etc/cherrymountain`. The federate unit (`cm-federate@.service`), its
    settings, and `helics.json` are injected by the `cherrymountain` app (the
    ci topology still injects `cm-cellar.service` statically).
- **Build:** the commands above, with `<name>` set to `cm-sim`. The build
  downloads from PyPI, GitHub, `artifacts.opensearch.org`, `apt.grafana.com`,
  and `grafana.com`.
- **Used by:** `sim-ctl`, `sim-process`, `sim-power`, and `sim-building` (ci:
  `sim-ctl` and `sim-process`).
- **Pinned versions:** `helics==3.6.1`; `CM_REF` (a commit of this repository;
  move it forward with any `sim/` change the image must carry);
  `opensearch=3.9.0` (apt, held; repository key
  `A8B2D9E04CD51FEF6AA2DB53BA81D99981191457`); Grafana `12.*` (repository key
  `B53AE77BADB630A683046005963FA27710458545`); `grafana-opensearch-datasource`
  `2.34.4`. OpenDSSDirect.py follows the package's `~=0.8.4`.
- **Ground-truth timestamps** are the federate clock (sim time, which starts on
  a summer weekday at 04:30 by default), not wall time. The dashboard opens on
  a two-day window around now; pick the sim date when the clock was moved.

## cm-otsim

- **Purpose:** ot-sim field devices: the remote I/O servers (`fd-server`) that
  speak Modbus to the PLCs and HELICS to the simulation, the BAS controllers
  that speak BACnet/IP, the DER gateway (`rtu-der`, a FEP with a DNP3
  outstation), and the utility DNP3 master.
- **Base:** the shared noble `minbase` image plus the ot-sim build
  dependencies, 10 GB disk. The build dependencies are purged at the end;
  `libczmq4`, `libzmq5`, and `libxml2` stay.
- **Contents:**
  - ot-sim built from source and installed under `/usr/local`, with the recipe
    the `otsim-noble` spike verified (DESIGN.md 12.3): the C message bus, the
    C++ DNP3 module (opendnp3 3.1.2) and the end-to-end DNP3 master
    `ot-sim-e2e-dnp3-master` (`-DBUILD_E2E=ON`; pydnp3 does not build on
    noble), the Go cpu, modbus, logic, and other modules, and the Python
    modules.
  - A venv at `/opt/otsim-venv` with `helics` and the ot-sim Python package,
    with `pyzmq` rebuilt from source (`--no-binary`) against the system
    libzmq. The build fails if the wheel's bundled libzmq survives.
  - `ot-sim.service`, which starts the CPU module after `phenix.service` and
    stays inactive until the ot-sim app injects `/etc/ot-sim/config.xml`.
  - BACpypes3 and pydantic in the same venv, for the BACnet module of the
    bas-* devices. The module itself is not in the image: the `cherrymountain`
    app injects `phenix-injects/otsim/cm_otsim_bacnet.py` to
    `/usr/local/lib/cm-otsim/` and adds it to the device's `config.xml`
    ([phenix-injects/otsim/README.md](../phenix-injects/otsim/README.md)), so
    a change to the module needs no image rebuild.
- **Build:** the commands above, with `<name>` set to `cm-otsim`. The build
  needs network access: it clones ot-sim, downloads the opendnp3 archives, and
  lets the Go command download the newer toolchain a module needs
  (`GOTOOLCHAIN=auto`). An offline build must pre-seed that toolchain and the
  module cache.
- **Used by:** every ot-sim node: the rio-* devices, glycol-chiller, the
  FIELD-PWR devices, rtu-der, utility-derms, and the bas-* devices (ci:
  `rio-cellar`).
- **Pinned versions:** `OTSIM_REF=9dc33047ff9fb474e61c8931c7dac7bf98df38ef`
  (the commit the spike and the integration test built), `helics==3.6.1`, the
  `pyzmq` version ot-sim's package resolves, `bacpypes3==0.0.110`, and
  `pydantic==2.11.7`.

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
  `Autonomy-Logic/openplc-runtime`), checked against its commit
  `9fb5e1a29b574806f5aea3a4e78202c7a5d658ee` so a moved tag fails the build. Whether `install.sh --native` works in the
  build chroot is marked `TODO(verify)`; `spikes/openplc-v4` runs the same
  install on a live VM.

## cm-fuxa

- **Purpose:** the FUXA SCADA server and HMI.
- **Base:** the shared noble `minbase` image plus `git`, `xz-utils`,
  `python3`, `build-essential`, and `libsqlite3-dev` for the build, 12 GB disk.
- **Contents:**
  - Node.js from the nodejs.org tarball, checked against its SHA-256 and
    unpacked under `/usr/local`.
  - FUXA at `/opt/fuxa` (one commit, built with `npm run build`, dev
    dependencies pruned) with the driver plugins installed so the range runs
    offline.
  - `fuxa.service`, which runs `node /opt/fuxa/server/main.js`.
  - `/etc/cherrymountain/fuxa`. The project JSON, `mysettings.json`, and
    `cm-fuxa-provision` are injected by the topology. The script sets the
    `admin` password, adds the `operator` account, and imports the project.
  - The Wazuh agent, disabled ([Wazuh agent](#wazuh-agent)).
- **Build:** the commands above, with `<name>` set to `cm-fuxa`.
- **Used by:** `scada-fuxa` in the ci variant.
- **Pinned versions:** Node.js `22.14.0` (tarball SHA-256
  `69b09dba...e80437ec`, from nodejs.org's `SHASUMS256.txt`); FUXA commit
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
  - `logrotate`, for the WireGuard peer event log (`/etc/logrotate.d/cm-wireguard`
    is injected by the topology).
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
  - `INSTALL_SSHD_HARDENING`: [SSH hardening](#ssh-hardening-on-the-vendor-path),
    with password logins off.
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
    the cherrymountain app's `50-cherrymountain.sql` creates `cmadmin`, and
    `cm_vendor_sql.py` (run by `cm-guacamole-start`) creates a named account per
    vendor technician, each with its own TOTP key and its own copies of the EWS
    connections.
  - A recordings directory (`/var/lib/cm-guacamole/recordings`, mode 2750,
    owner uid 1000, group 1001) that guacd writes, the web app reads, and the
    Wazuh agent watches.
  - The Wazuh agent, disabled.
  - The topology injects the Compose file, the nginx TLS front end, the units,
    and the settings from `phenix-injects/guacamole/`. TOTP and the recording
    storage extension are inside the pinned `guacamole/guacamole` image, and are
    switched on by environment variables.
  - `INSTALL_SSHD_HARDENING`: [SSH hardening](#ssh-hardening-on-the-vendor-path),
    with password logins off.
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
    brings the `phenix` port up (DESIGN.md 14). The web password of the user
    `malcolm` comes from the cherrymountain app (`malcolm_password` in the
    experiment's `secrets.json`, injected root-only as
    `/etc/default/cm-malcolm-credentials`, which only the provisioning unit
    reads); without the app the first boot generates one. Either way it is in
    `/var/lib/cm-malcolm/admin-password` on the VM.
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
    `80-cm-wazuh-manager.sh` turns on password enrollment, and its
    `/etc/default/cm-wazuh-manager` gives the dashboard's `admin` password
    (`wazuh_password` in `secrets.json`; the password tool generates the other
    users', and `/var/lib/cm-wazuh/passwords.txt` holds all of them).
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
  - Chromium from the xtradeb PPA (`ppa:xtradeb/apps`), which builds the
    current release for noble against noble's own libraries. Ubuntu's
    `chromium-browser` is a snap shim that cannot be installed offline, and
    Debian 13's chromium (the earlier source) no longer installs on noble: it
    needs `libharfbuzz0b` 10 and `libxcomposite1` 0.4.6, and apt does not take
    a dependency from a low-priority source. The PPA is added at pin priority
    100 and only `chromium`, `chromium-common`, and `chromium-sandbox` are
    raised to 990, so nothing else comes from it. A flags file in
    `/etc/chromium.d` adds `--no-sandbox` (the desktop runs as root) and
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
- **Pinned versions:** the Wazuh agent `4.14.10-1`. Chromium is not pinned: the
  PPA carries only its newest build (`156.0.8078.73-1xtradeb1.2404.1` when the
  dependencies were checked with `apt-get -s`), and the build log prints the
  version installed. The PPA key is checked against fingerprint
  `5301FA4FD93244FBC6F6149982BB6851C64F6880`.

## cm-ews

- **Purpose:** the engineering workstation (`ews`), and the one choke point from
  the DMZ into L1 (DESIGN.md 12.4 and 13).
- **Base:** the phenix `mingui` variant on noble, plus `xrdp`, `xorgxrdp`,
  `wireshark`, `tshark`, `mbpoll`, `polkitd`, `python3-venv`, `zip`, `unzip`, and
  `gnupg`, 28 GB disk.
- **Contents:**
  - OpenPLC Editor 4.3.4, the Linux AppImage unpacked to `/opt/openplc-editor`
    (no FUSE) with a launcher that passes `--no-sandbox`.
  - Chromium from the xtradeb PPA, the same way as in `cm-desktop`, for the
    FUXA editor and the OpenPLC REST UI.
  - Wireshark and `mbpoll`, and a venv `/opt/ews-venv` with BAC0, pymodbus, and
    asyncua (whose command line tools `uals`, `uaread`, `uawrite`, and `uacall`
    stand in for an OPC UA client).
  - xrdp, enabled, with a polkit rule for the color-manager prompt and
    `xfce4-session` in `/etc/skel/.xsession`, so an RDP login lands in Xfce.
  - The Wazuh agent, disabled.
  - The topology injects the vendor account setup (`phenix-injects/ews/`). It
    creates the technicians' accounts in the group `cm-engineers`, which may
    read the PLC credential in `/etc/default/cm-ews`, so `cm_plc_tool.py` runs
    as the technician.
  - `INSTALL_SSHD_HARDENING`: [SSH hardening](#ssh-hardening-on-the-vendor-path),
    with password logins kept for the vendor account, and xrdp refusing root.
- **Build:** the commands above, with `<name>` set to `cm-ews`.
- **Used by:** `ews`.
- **Pinned versions:** OpenPLC Editor `4.3.4` (SHA-256 `b8186372...ec75fa0`),
  `BAC0==2026.7.25`, `pymodbus==3.16.2`, `asyncua==2.1.0`, the Wazuh agent
  `4.14.10-1` (Chromium as in `cm-desktop`). `opcua-client` is not used: its current release needs
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
  - A trimmed `/etc/mosquitto/mosquitto.conf` (pid file, log file, and `include_dir`
    only). The packaged one also sets `persistence_location`, and the app's drop-in sets
    it again, which makes Mosquitto stop with "Duplicate persistence_location".
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

## SSH hardening on the vendor path

`cm-ews`, `cm-guacamole`, and `cm-wireguard` run `INSTALL_SSHD_HARDENING` after
their install script (DESIGN.md 13 and 15.7). The phenix default
`POSTBUILD_NO_ROOT_PASSWD` turns on root SSH with an empty password; the script
adds `/etc/ssh/sshd_config.d/00-cm-hardening.conf`, which wins because sshd
keeps the first value it reads and noble's `sshd_config` includes that
directory first:

- `PermitRootLogin no`, `PermitEmptyPasswords no`, `MaxAuthTries 3`,
  `LoginGraceTime 30`, and no X11, agent, or TCP forwarding or tunnels.
- `PasswordAuthentication no` on the DMZ hosts (nobody logs in over SSH);
  `yes` on `cm-ews` for the vendor account's Guacamole SSH session. `cm-ews`
  also sets xrdp's `AllowRootLogin=false`, because PAM's `nullok` would let
  root in with an empty password over RDP.
- The script runs `ssh-keygen -A` and `sshd -t`, so a bad drop-in fails the
  build.

The passwordless root console (VNC and cc) stays on every image for the white
cell: an accepted lab-only risk, reachable only from the phenix host.
