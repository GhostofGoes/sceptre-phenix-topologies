# phenix images

This directory has one phenix `Image` configuration for each VM role of the
CherryMountain Brewery topology. This file has one section for each image.
Each section has the same items in a fixed order: purpose, base, contents,
build, used by, and pinned versions. When a change adds an image
configuration, add a section here in the same change.

Each file is the stored form of an image, which is the text that
`phenix config get image/<name>` prints. The `POSTBUILD_*` scripts in the
files are exact copies of the defaults that `phenix image create` adds
(`src/go/api/image/constants.go`). Thus the lint hooks skip them. Only the
`INSTALL_*` script of each image is ours.

Every image is Ubuntu 24.04 (noble) in the qcow2 format. The build uses the
`us.archive.ubuntu.com` mirror with the `main restricted universe multiverse`
components. All images are `minbase` except `cm-desktop` and `cm-ews`, which
are `mingui` (xubuntu, with the phenix GUI autologin). `phenix config create`
stores a configuration as it is, and only `phenix image create` adds the
packages of a variant. Thus those two files list `xubuntu-desktop`, `wmctrl`,
and `xdotool` themselves.

All images share this package set: `ca-certificates chrony curl dbus
ethtool init initramfs-tools iproute2 iputils-ping isc-dhcp-client less
linux-headers-generic linux-image-generic ncat net-tools netbase nftables
openssh-server procps rsync ssh tcpdump tmux vim wget`. The sections list only
what each image adds.

The configurations pin the base only to the release name, and the base is the
release pocket only. A pocket is one package archive of an Ubuntu release. The
debootstrap tool gets one suite, and no script adds `noble-updates` or
`noble-security`. As a result, the base packages are those of April 2024 (for
example `openssh-server` 1:9.6p1-3ubuntu13 and kernel 6.8.0-31). The decision
to build from the update pockets or not is open
([FUTURE_WORK.md](../docs/FUTURE_WORK.md#images-and-builds)).

Build any image from the repository root, in the phenix container. The
container has vmdb2 and debootstrap. The build itself needs internet access
and does not need `/dev/kvm`. `inject-miniexe` works only while minimega runs.
The result is `<dir>/<name>.qc2`. Replace `<name>` with the image name:

```shell
phenix config create cherrymountain-brewery/phenix-images/<name>.yml
phenix image build -o <dir> -c -x <name>
phenix image inject-miniexe /usr/local/bin/miniccc <dir>/<name>.qc2
```

`inject-miniexe` adds `miniccc`, which SoH, cc, and SCORCH need (DESIGN.md
15.7). The topology refers to the result as `<name>.qc2`.

The lint hooks (see `prek.toml`) do three things:

- They make sure that these files obey the sceptre-phenix `Image` schema.
- They run shellcheck on every `INSTALL_*` script.
- They run `systemd-analyze verify` on the units that those scripts write, and
  they apply an exposure limit to those units.

Every `INSTALL_*` script must allow for the way that phenix runs the scripts:

- From the scripts of `script_order`, phenix makes one script and drops its
  blank lines. Then vmdb2 runs that script with `sh -ec` in the chroot of the
  image. A chroot is a directory that a process uses as its root. On Ubuntu,
  `sh` is dash, so the script cannot use bash syntax. One failed command ends
  the build. A variable or a `cd` of one script carries into the next script.
- First, vmdb2 renders the script with Jinja2. Thus `{{`, `{%`, and `{#` (as
  in `${#name}`) must not appear.
- `phenix config create` replaces each `${NAME}` and `${NAME:default}` with
  the value from the phenix host environment, or with an empty string. Write a
  shell variable as `$NAME`.
- The chroot has `/proc`, `/sys`, `/dev`, and the build host's `/run`, and the
  host's `resolv.conf`. No systemd runs in it: `systemctl enable` and
  `disable` work, and `start` does nothing.
- With `-c`, vmdb2 keeps the debootstrapped root, with the packages of the
  `packages` list installed, in `<dir>/<name>.tar`. It starts the next build
  from that file. After a change to `packages`, delete that file.

The image lint hook makes sure that each file obeys the first three points and
that a `mingui` file lists its packages. Build in the phenix container.
`xubuntu-desktop` recommends the `firefox` package of Ubuntu. When the build
host has a snapd (`/run/snapd.socket`), that package installs a snap through
it. When the build host has none, the package skips the snap.

## The brand

No image carries the mark, the wallpaper, or a color of the brand
([brand/](../brand/README.md)). The image build does not use this repository.
Thus the brand reaches the VMs as injections at boot, from the copies that
`sync.py` keeps in `phenix-injects/brand/`. An injection is a file that phenix
adds to a VM. The image configurations provide only what those injections
need, and they do not change the exact copies of the `POSTBUILD_*` defaults.

| Image | What the brand does there | What the image provides |
|---|---|---|
| `cm-fuxa` | The brand puts the tab icon over `/opt/fuxa/client/dist/favicon.ico` and the mark at `/opt/fuxa/server/_images/Brand/icon.svg`. `66-fuxa-brand.sh` writes the page title into `client/dist/index.html`. | The image provides FUXA at the pinned commit. At that commit, FUXA tracks its built client in `client/dist` and serves `server/_images` at `/_images`. |
| `cm-historian`, `cm-sim` | `cm-grafana-brand` copies the mark, tab icon, touch icon, and sign-in background over Grafana's own image files under `/usr/share/grafana/public`. We compared the script with the `grafana_12.4.14` package, which is the version that the images pin. | The images provide Grafana from the vendor's APT repository. `cm-sim` does not carry the ground-truth dashboard now. The app renders the dashboard with the series colors and injects it. The ground truth is the real state of the plant. |
| `cm-desktop`, `cm-ews` | The brand puts the wallpaper at `/usr/share/backgrounds/cherrymountain.png`. `cm-brand-wallpaper` (an XDG autostart entry) sets it for every session, which includes the vendor's xrdp session on `ews`. | `/root/.init.sh` (the phenix `POSTBUILD_GUI` default) sets its stock wallpaper on `monitorVirtual-1` ten seconds after login. The autostart script looks again until its own wallpaper stays. The desktops run at 1440x900, so they take the 1920x1080 wallpaper. |
| `cm-ews` | At boot, `cm-brand-xrdp` sets the title, colors, and logo of xrdp's login screen in the `[Globals]` section of `/etc/xrdp/xrdp.ini`. Then it restarts xrdp. | The image provides `xrdp` from noble (0.9.24). Its `xrdp.ini` has those keys, and it reads a BMP logo. |
| `cm-guacamole` | `docker-compose.yml` mounts the branding extension `cherrymountain-brand.jar` into the `guacamole/guacamole:1.6.0` container. | Nothing. The entrypoint of the container reads the extension. |

These screens carry no brand, because they are not the brewery's screens:
Malcolm and Wazuh (a SOC's own tools), the web interface of the OpenPLC
runtime (a PLC's own), and the web interface of InfluxDB.

## cm-sim

- Purpose: This image is for the simulation nodes: the HELICS root broker, the
  cherrymountain_sim federates, and on sim-ctl the ground truth (DESIGN.md
  9.5). A federate is one simulator of a HELICS co-simulation.
- Base: The base is the shared noble `minbase` image plus `gnupg`, `jq`,
  `logrotate`, `python3`, `python3-venv`, and `python3-pip`. The disk is 16 GB
  (the OpenSearch package alone is about 1 GB).
- Contents:
  - The image has a venv at `/opt/cherrymountain/venv`. A venv is an isolated
    Python environment. The venv has the `helics` wheel, which also ships
    `helics_broker`, `helics_app`, `helics_player`, and `helics_recorder`. The
    build links those four programs into `/usr/local/bin`. The venv also has
    the `cherrymountain-sim[power]` package from the `sim/` directory of this
    repository at `CM_REF`. That package brings `pydantic`, SimPy, and
    OpenDSSDirect.py.
  - SciPy, PackMLpy, and `ht` are for tests only, and the build does not
    install them.
  - The same venv has `websockets` and cm-viz. The cm-viz program is the
    `cherrymountain-viz` package from the `viz/` directory of this repository
    at `CM_REF`. The package includes its own copy of three.js. The
    `cherrymountain` app injects the unit of cm-viz on sim-ctl only.
  - OpenSearch holds the ground-truth indexes `cherrymountain-truth-*`. It
    runs as a single node on `0.0.0.0:9200`, with the security plugin disabled
    and a 2 GB heap.
  - Grafana has the `grafana-opensearch-datasource` plugin and a provisioned
    "Ground truth" data source on `127.0.0.1:9200`. The dashboard is not in
    the image. The `cherrymountain` app renders `cm-ground-truth.json` (its
    lines take the series colors of the brand) and injects it with
    `ground-truth.env`. Grafana serves an anonymous read-only view on port
    3000. The admin password comes from `ground-truth.env`.
  - The OpenSearch unit and the Grafana unit each carry a drop-in with
    `ConditionPathExists=/etc/cherrymountain/ground-truth.env`. A drop-in is a
    file that adds to the configuration of a unit. The `cherrymountain` app
    injects the file that the condition names on sim-ctl, in the full and lite
    variants only. Thus the other sim nodes and the ci variant never start the
    two units.
  - The image has the directory `/etc/cherrymountain`. The `cherrymountain`
    app injects the federate unit (`cm-federate@.service`), its configuration,
    and `helics.json`. The ci topology still injects `cm-cellar.service`
    statically.
- Build: Use the commands above, with `<name>` set to `cm-sim`. The build
  downloads from PyPI, GitHub, `artifacts.opensearch.org`, `apt.grafana.com`,
  and `grafana.com`.
- Used by: `sim-ctl`, `sim-process`, `sim-power`, and `sim-building`. In ci,
  the users are `sim-ctl` and `sim-process`.
- Pinned versions:
  - `helics==3.6.1`.
  - `CM_REF` (a commit of this repository) with `CM_SIM_TREE` and
    `CM_VIZ_TREE`. Those two values are the git tree ids of the two packages
    (`sim/cherrymountain_sim`, `viz/cherrymountain_viz`) at that commit. After
    a change to either package, a second commit moves all three values
    (`git rev-parse <commit>:cherrymountain-brewery/sim/cherrymountain_sim`).
    A test fails until that commit exists, because federates of an older
    `sim/` stop on the names of a newer `helics.json`.
  - `opensearch=3.9.0` (apt, held, with the repository key
    `A8B2D9E04CD51FEF6AA2DB53BA81D99981191457`).
  - Grafana `12.4.14` (repository key
    `B53AE77BADB630A683046005963FA27710458545`).
  - `grafana-opensearch-datasource` `2.34.4`.
  - `websockets==15.0.1`.
  - OpenDSSDirect.py follows the `~=0.8.4` of the package.
- Ground-truth timestamps: The timestamps (`@timestamp`) are the wall clock,
  which is the same clock that Malcolm and Wazuh stamp their records with. The
  simulated date and time is in `sim_time`. The dashboard opens on a two-day
  window around now.
- Also in the image: OpenSearch has a start timeout of 300 s and a restart
  (the package gives it 75 s and no restart). A logrotate rule handles the
  HELICS logs in `/var/log`.

## cm-otsim

- Purpose: This image is for the ot-sim field devices:
  - The remote I/O servers (`fd-server`), which speak Modbus to the PLCs and
    HELICS to the simulation.
  - The BAS controllers, which speak BACnet/IP.
  - The DER gateway (`rtu-der`, a FEP with a DNP3 outstation). A FEP is a
    front-end processor.
  - The utility DNP3 master.
- Base: The base is the shared noble `minbase` image plus the build
  dependencies of ot-sim, with a 10 GB disk. The build purges the build
  dependencies at the end, but `libczmq4`, `libzmq5`, and `libxml2` stay.
- Contents:
  - The build compiles ot-sim from source and installs it under `/usr/local`.
    It uses the recipe that the `otsim-noble` spike proved (DESIGN.md 12.3). A
    spike is a small test that answers one design question. The recipe builds
    these parts:
    - The C message bus.
    - The C++ DNP3 module (opendnp3 3.1.2).
    - The end-to-end DNP3 master `ot-sim-e2e-dnp3-master` (`-DBUILD_E2E=ON`),
      because pydnp3 does not build on noble.
    - The Go modules: cpu, modbus, logic, and others.
    - The Python modules.
  - A venv at `/opt/otsim-venv` has `helics` and the ot-sim Python package.
    The build rebuilds `pyzmq` from source (`--no-binary`) against the system
    libzmq. If the libzmq that the wheel bundles survives, the build fails.
  - `ot-sim.service` starts the CPU module after `phenix.service`. It stays
    inactive until the ot-sim app injects `/etc/ot-sim/config.xml`.
  - The same venv has BACpypes3 and pydantic, for the BACnet module of the
    bas-* devices. The module itself is not in the image. The `cherrymountain`
    app injects `phenix-injects/otsim/cm_otsim_bacnet.py` to
    `/usr/local/lib/cm-otsim/` and adds it to the `config.xml` of the device
    ([phenix-injects/otsim/README.md](../phenix-injects/otsim/README.md)).
    Thus a change to the module needs no image rebuild.
- Build: Use the commands above, with `<name>` set to `cm-otsim`. The build
  needs network access. It clones ot-sim and downloads the opendnp3 archives.
  It also lets the Go command download the newer toolchain that a module needs
  (`GOTOOLCHAIN=auto`). An offline build must pre-seed that toolchain and the
  module cache.
- Used by: Every ot-sim node uses this image: the rio-* devices,
  glycol-chiller, the FIELD-PWR devices, rtu-der, utility-derms, and the bas-*
  devices. In ci, the user is `rio-cellar`.
- Pinned versions: The image pins
  `OTSIM_REF=9dc33047ff9fb474e61c8931c7dac7bf98df38ef`, which is the commit
  that the spike and the integration test built. It also pins
  `helics==3.6.1`, the `pyzmq` version that the ot-sim package resolves,
  `bacpypes3==0.0.110`, and `pydantic==2.11.7`.

## cm-openplc

- Purpose: This image is the OpenPLC v4 runtime of the five PLCs (in ci, of
  plc-cellar alone). The unit of the runtime starts after the phenix startup
  scripts. As a result, a stored program from an earlier boot does not start
  before the node has its addresses.
- Base: The base is the shared noble `minbase` image plus `build-essential`,
  `python3`, `python3-pip`, and `python3-venv`, with a 12 GB disk. The runtime
  compiles uploaded programs on the VM, so the toolchain stays.
- Contents:
  - The build clones `openplc-runtime` to `/opt/openplc-runtime` and installs
    it with `install.sh --native`.
  - The image has `openplc-runtime.service`, which is the unit that
    `install.sh` writes when systemd runs. Systemd does not run in the chroot
    of the image build.
  - The image has `cmake` from noble (3.28, the lowest version that
    `install.sh` accepts). Without that package, `install.sh` downloads a
    cmake from GitHub without a checksum. The image also has `git`, `zip`, and
    `unzip` for the provisioning script. If `install.sh` did not produce the
    runtime, the S7comm plugin, or one of the four venvs, the build fails.
  - The image has the directories `/var/lib/cm-openplc`,
    `/opt/cherrymountain/openplc`, and `/var/lib/openplc-runtime`. The
    topology injects the program ZIP, the plugin configurations, and
    `cm-openplc-provision`.
- Build: Use the commands above, with `<name>` set to `cm-openplc`. Build the
  program ZIPs separately (`plc/common/build_zip.sh --all` for full and lite,
  `plc/plc-cellar/build_zip.sh` for ci).
- Used by: The five PLCs (`plc-brewhouse`, `plc-cellar`, `plc-packaging`,
  `plc-utilities`, `plc-power`) in lite and full, and `plc-cellar` in ci.
- Pinned versions: The image pins `OPENPLC_REF=v4.3.2` (a tag of
  `Autonomy-Logic/openplc-runtime`). The build makes sure that the tag points
  to its commit `9fb5e1a29b574806f5aea3a4e78202c7a5d658ee`, so a moved tag
  fails the build. The question of whether `install.sh --native` works in the
  build chroot has the mark `TODO(verify)`. The retired `openplc-v4` spike ran
  the same install in a container.

## cm-fuxa

- Purpose: This image is the FUXA SCADA server and HMI.
- Base: The base is the shared noble `minbase` image plus `git`, `xz-utils`,
  `python3`, `build-essential`, and `libsqlite3-dev` for the build, with a
  12 GB disk.
- Contents:
  - Node.js comes from the nodejs.org tarball. The build makes sure that the
    tarball matches its SHA-256 and unpacks it under `/usr/local`.
  - FUXA is at `/opt/fuxa`. The build takes one commit, runs `npm run build`,
    and prunes the dev dependencies. It installs the driver plugins, so the
    range runs offline.
  - `fuxa.service` runs `node /opt/fuxa/server/main.js`.
  - The image has the directory `/etc/cherrymountain/fuxa`. The topology
    injects the project JSON, `mysettings.json`, and `cm-fuxa-provision`. That
    script sets the `admin` password, adds the `operator` account, and imports
    the project.
  - The image has the Wazuh agent, disabled ([Wazuh agent](#wazuh-agent)).
- Build: Use the commands above, with `<name>` set to `cm-fuxa`.
- Used by: `scada-fuxa` and `bas-fuxa` in lite and full, and `scada-fuxa` in
  ci.
- Pinned versions: The image pins Node.js `22.14.0` (tarball SHA-256
  `69b09dba...e80437ec`, from the `SHASUMS256.txt` of nodejs.org). It pins the
  FUXA commit `b2972583b3d59525131c44e8b3214f55b74750fe` (server 1.3.6). It
  pins the plugins `modbus-serial@8.0.19`, `node-opcua@2.149.0`,
  `node-snap7@1.0.9`, and `node-bacnet@0.2.4`. The build installs the other
  npm dependencies with `npm install`. Thus they follow the lockfile of FUXA
  only where it holds them, and nothing pins the dependencies of the plugins.

## cm-svc

- Purpose: This image is for the hosts of the small network services. The host
  ot-svc provides NTP, DNS, a syslog relay, and the OPS BBMD. A BBMD is a
  BACnet device that forwards broadcasts between subnets. The host bas-bbmd is
  the BAS BBMD. The host inet-traffic provides the name and time service of
  the internet side, and the protonuke server.
- Base: The base is the shared noble `minbase` image plus `dnsmasq`,
  `rsyslog`, `python3`, and `python3-venv`, with an 8 GB disk.
- Contents:
  - A venv at `/opt/bacnet` has BACpypes3 and `pydantic`, and the
    `cm-bbmd.service` unit runs from that venv. The topology injects the unit,
    `cm_bbmd.py`, and its configuration (`phenix-injects/bbmd/`). The startup
    hook enables the unit on ot-svc and bas-bbmd only.
  - The build installs `dnsmasq` and disables it. The injected plant zone
    (`phenix-injects/ot-svc/`, `phenix-injects/inet-traffic/`) and the startup
    hooks configure and start it.
  - The image has the directories `/etc/cherrymountain` and
    `/usr/local/lib/cm-bbmd`.
  - The image has the Wazuh agent, disabled ([Wazuh agent](#wazuh-agent)).
- Build: Use the commands above, with `<name>` set to `cm-svc`. The host
  inet-traffic uses a copy of the image with protonuke added. To make that
  copy, copy `cm-svc.qc2` to `cm-svc-protonuke.qc2`. Then run
  `phenix image inject-miniexe /usr/local/bin/protonuke <dir>/cm-svc-protonuke.qc2`.
- Used by: `ot-svc`, `bas-bbmd`, and (as `cm-svc-protonuke.qc2`)
  `inet-traffic`.
- Pinned versions: The image pins `bacpypes3==0.0.110` (the version of the
  BBMD spike), `pydantic==2.11.7`, and the Wazuh agent (see
  [Wazuh agent](#wazuh-agent)).

## Wazuh agent

These images carry the Wazuh agent: `cm-svc`, `cm-fuxa`, `cm-wireguard`,
`cm-guacamole`, `cm-malcolm`, `cm-desktop`, `cm-ews`, and `cm-historian`.
DESIGN.md 14 lists the Linux hosts in OPS, HMI, BAS, the DMZ, and the SOC. All
of the images install the agent the same way, and none enables it:

- The install script fetches the APT key and makes sure that it matches the
  fingerprint that Wazuh publishes
  (`0DCFCA5547B19D2A6099506096B3EE5F29111145`). The repository is a deb822
  source with `Signed-By`.
- The install script pins the package to `4.14.10-1`, which is the version of
  the `cm-wazuh` manager. An agent must not be newer than its manager, so
  change the two versions together.
- At boot, the `80-cm-wazuh-agent.sh` of the cherrymountain app points the
  agent at the manager and enrolls it. The enrollment uses a password, and the
  group is the `zone` label of the node. The script then enables the service.
  Until then, the service is disabled.

The PLCs, the ot-sim devices, the simulation nodes, and the routers have no agent
(DESIGN.md 14).

## cm-wireguard

- Purpose: This image is the WireGuard gateway of the DMZ (`dmz-vpn`). The
  gateway is the DNAT target of fw-edge for the vendor tunnel (DESIGN.md 13).
- Base: The base is the shared noble `minbase` image plus `wireguard-tools`,
  `gnupg`, `python3`, `python3-pip` and pydantic 2.11.7 (pip), and `rsyslog`.
  The disk is 8 GB. The configuration is the focal
  `renewables/wind/plant/phenix-images/wireguard.yml`, rebased on noble.
- Contents:
  - The image has `logrotate`, for the log of WireGuard peer events. The
    topology injects `/etc/logrotate.d/cm-wireguard`.
  - The image has `wireguard-tools`. The kernel has WireGuard.
  - The image has no forwarding configuration. The host dmz-vpn forwards the
    decapsulated tunnel traffic onto the DMZ without NAT. Its startup hook
    turns `net.ipv4.ip_forward` on only after the nftables forward policy
    loads.
  - The image has the directories `/etc/wireguard` (mode 0700) and
    `/usr/local/lib/cm-wireguard`.
  - The image has the Wazuh agent, disabled.
  - The topology injects the rest from `phenix-injects/wireguard/`:
    - The nftables policy that pins the vendor peer to dmz-jump on TCP 443.
    - `cm_wg_watch.py` with its unit. The script writes a line for every
      tunnel handshake, new endpoint, and timeout, and Wazuh reads those
      lines.
    - The startup hook.
  - The phenix `wireguard` app injects `wg0.conf` and enables `wg-quick@wg0`.
  - `INSTALL_SSHD_HARDENING` applies the
    [SSH hardening](#ssh-hardening-on-the-vendor-path), with password logins
    off.
- Build: Use the commands above, with `<name>` set to `cm-wireguard`.
- Used by: `dmz-vpn`.
- Pinned versions: The image pins the Wazuh agent at `4.14.10-1`.
  `wireguard-tools` and `python3-pip` follow noble. The image pins pydantic at
  2.11.7. The pydantic of noble is 1.10, and the scripts of the range cannot
  use that version.

## cm-guacamole

- Purpose: This image is the Guacamole jump host (`dmz-jump`). The jump host
  is the one conduit from the DMZ into the EWS for vendor sessions, with TOTP
  and session recording (DESIGN.md 13). The EWS is the engineering
  workstation.
- Base: The base is the shared noble `minbase` image plus Docker, `skopeo`,
  `openssl`, `gnupg`, `python3`, `python3-pip` and pydantic 2.11.7 (pip), and
  `rsyslog`. The disk is 16 GB.
- Contents:
  - The image has Docker Engine and the Compose plugin from the APT repository
    of Docker.
  - The image has four container images as compressed copies on disk under
    `/var/lib/cm-guacamole/images`. `/etc/cherrymountain/images.list` lists
    them. A build has no Docker daemon. Thus `cm-load-images.service`
    (injected, from `phenix-injects/docker/`) loads the copies into Docker at
    the first boot and then deletes them.
  - The stock PostgreSQL schema (`001-create-schema.sql`) is in
    `/opt/cherrymountain/guacamole/initdb`. The build makes sure that the
    schema matches the Apache checksum.
  - The image deliberately leaves out the stock `guacadmin` account
    (`002-create-admin-user.sql`). The `50-cherrymountain.sql` of the
    cherrymountain app creates `cmadmin`. `cm_vendor_sql.py`, which
    `cm-guacamole-start` runs, creates a named account for each vendor
    technician. Each account has its own TOTP key and its own copies of the
    EWS connections.
  - The image has a recordings directory
    (`/var/lib/cm-guacamole/recordings`, mode 2750, owner uid 1000, group
    1001). The guacd daemon writes to that directory, the web app reads it,
    and the Wazuh agent watches it.
  - The image has the Wazuh agent, disabled.
  - The topology injects the Compose file, the nginx TLS front end, the units,
    and the configuration from `phenix-injects/guacamole/`. TOTP and the
    recording storage extension are inside the pinned `guacamole/guacamole`
    image, and environment variables switch them on.
  - `INSTALL_SSHD_HARDENING` applies the
    [SSH hardening](#ssh-hardening-on-the-vendor-path), with password logins
    off.
- Build: Use the commands above, with `<name>` set to `cm-guacamole`. The
  build needs internet access for Docker Hub, the APT repository of Docker,
  and archive.apache.org.
- Used by: `dmz-jump`.
- Pinned versions: The image pins Docker `5:29.9.3-1~ubuntu.24.04~noble` and
  the Compose plugin `5.7.0-1~ubuntu.24.04~noble`. It pins the container
  images `guacamole/guacd:1.6.0`, `guacamole/guacamole:1.6.0`,
  `postgres:17.11-alpine`, and `nginx:1.30.6-alpine`. It pins the Guacamole
  JDBC schema from `guacamole-auth-jdbc-1.6.0.tar.gz` (SHA-256
  `97bc5fd3...a7039366`). The pins of the container images are tags, not
  digests.

## cm-malcolm

- Purpose: This image is the Malcolm network sensor (`malcolm`): Zeek with the
  ICSNPP parsers, Suricata, Arkime, and OpenSearch. The sensor captures from
  the feed of the mirror app (DESIGN.md 14). Zeek and Suricata read the port
  live, and Arkime indexes the rotating pcap files of the capture container of
  Malcolm. This is the largest image.
- Base: The base is the shared noble `minbase` image plus Docker, Open
  vSwitch, `git`, `gnupg`, `skopeo`, `jq`, `openssl`, and `apache2-utils`. It
  also has `python3-pip` and pydantic 2.11.7 (pip), `python3-venv`, and
  `rsyslog`. The disk is 64 GB.
- Contents:
  - The image has Docker Engine and the Compose plugin from the APT repository
    of Docker.
  - Malcolm's own checkout is at `/opt/malcolm`, without `.git`. The checkout
    is the commit of the `v26.10.0` tag, and the build makes sure that the tag
    points to that commit. Uid 1000 (the user `malcolm`) owns the checkout. A
    venv `/opt/malcolm-venv` has the Python modules that the configurator of
    Malcolm and `auth_setup` need.
  - The image has the 22 Malcolm images of the full profile as compressed
    copies on disk under `/var/lib/cm-malcolm/images` (7.4 GB).
    `cm-load-images.service` loads them into Docker at the first boot.
  - One image serves both sizing profiles. The full profile has 8 vCPU and
    48 GB in the topology, and 24 GB at the least. The slim profile has 4 vCPU
    and 12 GB. The profiles differ in their configuration file
    (`phenix-injects/malcolm/malcolm-config-<profile>.yaml`), the services
    that start, and the heap sizes (`profiles.sh`). `CM_MALCOLM_PROFILE=auto`
    picks the profile by memory, and the OpenSearch heap of the full profile
    follows the memory too.
  - The image has Open vSwitch, enabled, for the `setupOVS` of the mirror app.
  - The image has the host configuration of the installer of Malcolm: all of
    its sysctl values (among them `vm.max_map_count` and
    `vm.overcommit_memory`) and its limits.
  - The image has the Wazuh agent, disabled.
  - The topology injects the sensor from `phenix-injects/malcolm/`. The first
    boot (`cm-malcolm-provision`) runs Malcolm's own configurator on the
    configuration file of the profile
    (`scripts/configure --non-interactive --import-malcolm-config-file`). The
    configurator writes `config/*.env` and the compose file, with the restart
    policy.
  - The first boot then applies the four fixes of the malcolm spike:
    - It creates `zeek/custom/__load__.zeek` (the bind mount hides the file of
      the image).
    - It sets `PCAP_PIPELINE_VERBOSITY` in `config/upload-common.env`.
    - It refuses custom Suricata rules with a sid below 9000101.
    - `cm-malcolm-wait-ready` waits for the ZeroMQ subscriptions of the
      processors before the first pcap.
  - The first boot also installs `cm-suricata.yaml` in
    `suricata/include-configs/` and sets one Zeek worker (DESIGN.md 14). With
    that file, the flow key of Suricata has no VLAN, and Suricata allows
    one-sided streams. Mirrored traffic needs both.
  - The capture unit adds the OpenFlow flow
    (`in_port=sniffer,actions=output:phenix`) and brings the `phenix` port up
    (DESIGN.md 14).
  - The web password of the user `malcolm` comes from the cherrymountain app
    (`malcolm_password` in the `secrets.json` of the experiment). The app
    injects it for root only as `/etc/default/cm-malcolm-credentials`, and
    only the provisioning unit reads that file. Without the app, the first
    boot generates a password. In both cases, the password is in
    `/var/lib/cm-malcolm/admin-password` on the VM.
- Build: Use the commands above, with `<name>` set to `cm-malcolm`. The build
  downloads 7.4 GB from `ghcr.io` and needs the 64 GB disk.
- Used by: `malcolm`.
- Pinned versions: The image pins Malcolm `v26.10.0` (commit
  `36bb3a61217b8555bb9fe8918acd3b45927a5458`) and its images at `26.10.0`. It
  pins Docker `5:29.9.3-1~ubuntu.24.04~noble` and the Compose plugin
  `5.7.0-1~ubuntu.24.04~noble`. In the venv, it pins `kubernetes==37.0.2`,
  `python-dotenv==1.2.4`, `pyyaml==6.0.3`, `requests==2.34.2`, and
  `ruamel.yaml==0.19.1`.

## cm-wazuh

- Purpose: This image is the SIEM (`wazuh`): the Wazuh manager, indexer, and
  dashboard on one VM (DESIGN.md 14).
- Base: The base is the shared noble `minbase` image plus `gnupg`, `jq`,
  `openssl`, `python3`, and `rsyslog`. The disk is 32 GB (the indexer keeps
  30 days of alerts and 7 days of archives, DESIGN.md 14).
- Contents:
  - The build installs `wazuh-manager`, `wazuh-indexer`, `wazuh-dashboard`,
    and the Filebeat of Wazuh from the packages, with all four services
    disabled. The Filebeat is 7.10.2, the build that pairs with the indexer.
  - Everything that depends on the address of the VM, the certificates, and
    the passwords happens at the first boot. `cm-wazuh-provision.service`
    (`phenix-injects/wazuh/`) does that work, so the range needs no internet.
  - `/opt/cherrymountain/wazuh` has the certificate tool, the password tool,
    the Filebeat template, and the Filebeat module of Wazuh. The build copies
    the two tools from the pinned `wazuh-indexer` package, which ships them.
    The build makes sure that the files match SHA-256 values. The template
    gives a daily index one primary shard, as Wazuh advises for one node.
  - The image sets an indexer heap of 2 GB
    (`/etc/wazuh-indexer/jvm.options.d/cm-heap.options`) and
    `vm.max_map_count`.
  - The topology injects the rules, the decoders, the configuration of the
    agent groups, the syslog listener, and the provisioning unit. The
    `80-cm-wazuh-manager.sh` of the cherrymountain app turns on password
    enrollment. The `/etc/default/cm-wazuh-manager` of the app gives the
    `admin` password of the dashboard (`wazuh_password` in `secrets.json`).
    The password tool generates the passwords of the other users, and
    `/var/lib/cm-wazuh/passwords.txt` holds all of the passwords.
- Build: Use the commands above, with `<name>` set to `cm-wazuh`.
- Used by: `wazuh`.
- Pinned versions: The image pins `wazuh-manager`, `wazuh-indexer`, and
  `wazuh-dashboard` at `4.14.10-1`, and Filebeat at `7.10.2-2`. It pins the
  helper files of the 4.14 release by SHA-256 (`wazuh-certs-tool.sh`,
  `wazuh-passwords-tool.sh`, `filebeat.yml`, `wazuh-template.json`,
  `wazuh-filebeat-0.5.tar.gz`).

## cm-desktop

- Purpose: This image is the workstation desktop for every role that has one:
  the HMI kiosks (`hmi-brewhouse`, `hmi-packaging`), `corp-ws`, `vendor-ws`,
  and `soc-analyst` (DESIGN.md 11).
- Base: The base is the phenix `mingui` variant (xubuntu, root autologin) on
  noble, plus `wireguard-tools`, `python3-pip` and pydantic 2.11.7 (pip),
  `gnupg`, and `rsyslog`. The disk is 24 GB.
- Contents:
  - Chromium comes from the xtradeb PPA (`ppa:xtradeb/apps`). The PPA builds
    the current release for noble against the libraries of noble itself. The
    `chromium-browser` of Ubuntu is a snap shim that you cannot install
    offline. The chromium of Debian 13 (the earlier source) does not install
    on noble now. It needs `libharfbuzz0b` 10 and `libxcomposite1` 0.4.6, and
    apt does not take a dependency from a low-priority source.
  - The build adds the PPA at pin priority 100. It raises only `chromium`,
    `chromium-common`, and `chromium-sandbox` to 990, so nothing else comes
    from the PPA. A flags file in `/etc/chromium.d` adds `--no-sandbox` (the
    desktop runs as root) and `--disable-gpu`.
  - The image has what the HMI kiosk needs, and nothing more: `chromium`
    (above), and `python3` (3.12) for the `cm-kiosk` script of the app. That
    script uses the standard library only. It does not depend on the pydantic
    2.11.7 that the image installs with pip for the other scripts of the
    range. The script starts Chromium with `--remote-debugging-pipe` (no port)
    and signs in to FUXA as `operator` for the browser
    ([HMI kiosk](https://github.com/GhostofGoes/sceptre-phenix-apps/blob/feat-cherrymountain-apps/src/python/phenix_apps/apps/cherrymountain/README.md#hmi-kiosk)).
  - A test with the packaged `/usr/bin/chromium` showed three facts. Its
    `exec` keeps descriptors 3 and 4 of the pipe.
    `/etc/chromium.d/cm-range` adds `--no-sandbox`, which the root session
    needs. The content script of an extension does not run under
    `--incognito`. For that reason, the kiosk does not use an extension.
  - The image has `wireguard-tools`, for `vendor-ws`. The phenix `wireguard`
    app injects `wg0.conf` and enables the tunnel.
  - The Xfce configuration turns off the screen saver, the lock screen, and
    display power management, because the root session has no password to
    unlock. The build also writes that configuration to
    `/etc/xdg/xdg-xubuntu`, over the power configuration of Xubuntu itself
    (display off after 15 minutes). Those Xubuntu values come first in the
    search path of the session.
  - The image has the Wazuh agent, disabled.
  - The roles differ only in the injected files:
    - The HMIs get the kiosk script, the desktop entry, and the URL of the
      app.
    - `corp-ws` and `vendor-ws` get protonuke.
    - `vendor-ws` gets the scripted vendor session
      (`phenix-injects/desktop/`).
    - `soc-analyst` gets the console launcher.
- Build: Use the commands above, with `<name>` set to `cm-desktop`. Then run
  `phenix image inject-miniexe /usr/local/bin/protonuke <dir>/cm-desktop.qc2`
  for the protonuke app.
- Used by: `hmi-brewhouse`, `hmi-packaging`, `corp-ws`, `vendor-ws`, and
  `soc-analyst`.
- Pinned versions: The image pins the Wazuh agent at `4.14.10-1`. It does not
  pin Chromium, because the PPA carries only its newest build. That build was
  `156.0.8078.73-1xtradeb1.2404.1` when we examined the dependencies with
  `apt-get -s`. We ran the debugging pipe and the sign-in of the HMI kiosk
  against that build, from its two packages, on noble. The build log prints
  the installed version. The build makes sure that the PPA key matches the
  fingerprint `5301FA4FD93244FBC6F6149982BB6851C64F6880`.

## cm-ews

- Purpose: This image is the engineering workstation (`ews`), which is also
  the one choke point from the DMZ into L1 (DESIGN.md 12.4 and 13).
- Base: The base is the phenix `mingui` variant on noble, plus `xrdp`,
  `xorgxrdp`, `wireshark`, `tshark`, `mbpoll`, `polkitd`, and `jq`. It also
  has `python3-pip` and pydantic 2.11.7 (pip), `python3-venv`, `zip`, `unzip`,
  `gnupg`, and `rsyslog`. The disk is 28 GB.
- Contents:
  - The image has OpenPLC Editor 4.3.4. The build unpacks the Linux AppImage
    to `/opt/openplc-editor` (no FUSE) and adds a launcher that passes
    `--no-sandbox`.
  - Chromium comes from the xtradeb PPA, the same way as in `cm-desktop`. It
    is for the FUXA editor and the OpenPLC REST UI.
  - The image has Wireshark and `mbpoll`. A venv `/opt/ews-venv` has BAC0,
    pymodbus, and asyncua. The command line tools of asyncua (`uals`,
    `uaread`, `uawrite`, and `uacall`) stand in for an OPC UA client.
  - The image has xrdp, enabled, with a polkit rule for the color-manager
    prompt. `xfce4-session` is in `/etc/skel/.xsession`, so an RDP login lands
    in Xfce.
  - The image has the Wazuh agent, disabled.
  - The topology injects the setup of the vendor account
    (`phenix-injects/ews/`). The setup creates the accounts of the technicians
    in the group `cm-engineers`. That group can read the PLC credential in
    `/etc/default/cm-ews`, so `cm_plc_tool.py` runs as the technician.
  - `INSTALL_SSHD_HARDENING` applies the
    [SSH hardening](#ssh-hardening-on-the-vendor-path). Password logins stay
    for the vendor account, and xrdp refuses root.
  - The build locks the password of root (`passwd -l root`). The technicians
    have ordinary accounts. Without the lock, the empty root password of the
    phenix default lets them `su` to root. The autologin of the console and cc
    do not use a password.
  - The screen saver, the lock, and the display power management of Xfce are
    off, as on `cm-desktop`.
- Build: Use the commands above, with `<name>` set to `cm-ews`.
- Used by: `ews`.
- Pinned versions: The image pins OpenPLC Editor `4.3.4` (SHA-256
  `27718205...124cdd2`), `BAC0==2026.7.25`, `pymodbus==3.16.2`,
  `asyncua==2.1.0`, and the Wazuh agent `4.14.10-1`. Chromium is as in
  `cm-desktop`. The image does not use `opcua-client`, because its current
  release needs Python 3.14, and noble has 3.12.

## cm-historian

- Purpose: This image is the plant historian (`historian`): InfluxDB 2,
  Telegraf, Grafana, and Mosquitto (DESIGN.md 12.4).
- Base: The base is the shared noble `minbase` image plus `mosquitto`,
  `mosquitto-clients`, `gnupg`, `jq`, `python3`, and `rsyslog`, with a 12 GB
  disk.
- Contents:
  - The image has InfluxDB 2 OSS and its CLI (`influxdb2`, `influxdb2-cli`),
    Telegraf, and Grafana from the APT repositories of their vendors, all
    enabled. The build makes sure that the repository keys match their
    fingerprints.
  - A Grafana drop-in gives anonymous Viewer access, because the HMI kiosks
    read Grafana without a login (DESIGN.md 10.3). The drop-in makes the plant
    dashboard the home page. It turns off plugin downloads, update checks, and
    the news feed, because the VMs have no internet.
  - The image has a trimmed `/etc/mosquitto/mosquitto.conf` (pid file, log
    file, and `include_dir` only). The packaged file also sets
    `persistence_location`, and the drop-in of the app sets it again. With
    both, Mosquitto stops with "Duplicate persistence_location".
  - The image has the Wazuh agent, disabled.
  - The cherrymountain app renders the rest at boot:
    - The credentials.
    - `telegraf.conf`.
    - The Mosquitto listener (one, on every address).
    - The Grafana data sources and dashboard.
    - The InfluxDB provisioning script (organization, buckets, token).
- Build: Use the commands above, with `<name>` set to `cm-historian`.
- Used by: `historian`.
- Pinned versions: The image pins `influxdb2` and `influxdb2-cli` at
  `2.9.1-1`, Telegraf at `1.40.3-1`, Grafana at `12.4.14`, and the Wazuh agent
  at `4.14.10-1`. We compared the brand script with that Grafana version.
  Mosquitto follows noble (2.0.x).

## kali-purple (upstream)

- Purpose: This image is the analysis workstation of the SOC, `soc-purple`
  (DESIGN.md 14). It is Kali Purple with Suricata, Wireshark, Docker, PEAT,
  and parseus.
- Base: This image is not a configuration in this directory, and it is not
  Ubuntu. It is the `kali-purple` target of
  [`sandialabs/sceptre-phenix-images`](https://github.com/sandialabs/sceptre-phenix-images)
  (kali-rolling, `mingui`, 80 GB). That repository added the target on
  2026-11-15.
- Contents: The `scripts/kali-purple.sh` of that repository is the install
  script. The image has no Wazuh agent and no chrony (ntpsec reads the
  `/etc/ntp.conf` that the phenix `ntp` app writes). It has nothing of this
  range in it, and the topology injects nothing.
- Build: Build in a checkout of that repository, on a host with Docker and
  internet access. Run `make kali-purple-bundle`. Then run
  `make kali-purple PARSEUS_OFFLINE=1`. Then move `kali-purple.qc2` to the
  image directory.

  The Makefile injects miniccc. The bundle is about 2 GB of container images.
  With the bundle, parseus can install at first boot without a network. An
  image without the bundle tries to pull images from the internet at every
  start, and it never finishes in the range.
- Used by: `soc-purple` in the full variant.
- Pinned versions: The pins are those of upstream: PEAT `v2026.9.2` by sha256,
  parseus `main` (not pinned), and Elasticsearch, Kibana, and Logstash
  `8.19.5` in the bundle of parseus. Kali is a rolling release, so two builds
  differ.

## Apps

The `cherrymountain` phenix app is in sceptre-phenix-apps
([`phenix_apps/apps/cherrymountain`](https://github.com/GhostofGoes/sceptre-phenix-apps/blob/feat-cherrymountain-apps/src/python/phenix_apps/apps/cherrymountain/README.md)).
The app has its own README for its metadata, stages, and tests. It injects
what differs between hosts of one image (PLC programs, FUXA projects, kiosk
scripts, passwords). Thus the images stay the same for every variant.

## SSH hardening on the vendor path

`cm-ews`, `cm-guacamole`, and `cm-wireguard` run `INSTALL_SSHD_HARDENING` after
their install script (DESIGN.md 13 and 15.7). The phenix default
`POSTBUILD_NO_ROOT_PASSWD` turns on root SSH with an empty password. The
hardening script adds `/etc/ssh/sshd_config.d/00-cm-hardening.conf`. That file
wins, because sshd keeps the first value that it reads, and the `sshd_config`
of noble includes that directory first. The file and the script do these
things:

- The file sets `PermitRootLogin no`, `PermitEmptyPasswords no`,
  `MaxAuthTries 3`, and `LoginGraceTime 30`. It allows no X11, agent, or TCP
  forwarding, and no tunnels.
- The file sets `PasswordAuthentication no` on the DMZ hosts, where nobody
  logs in over SSH. It sets `yes` on `cm-ews`, for the Guacamole SSH session
  of the vendor account. `cm-ews` also sets `AllowRootLogin=false` for xrdp,
  because the `nullok` of PAM otherwise lets root in over RDP with an empty
  password. It also locks the password of root, so no password path (su,
  polkit, xrdp, SSH) reaches root.
- The script runs `ssh-keygen -A` and `sshd -t`, so a bad drop-in fails the
  build.

The passwordless root console (VNC and cc) stays on every image for the white
cell. The white cell is the staff that runs the exercise. On `cm-ews`, the
console works through autologin, with the password of root locked. This
console is an accepted risk for a lab only, and only the phenix host can reach
it.
