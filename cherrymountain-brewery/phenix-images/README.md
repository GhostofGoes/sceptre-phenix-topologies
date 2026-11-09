# phenix images

One phenix `Image` config per VM role of the CherryMountain Brewery topology.
This file has one section per image, in a fixed order: purpose, base,
contents, build, used by, and pinned versions. Add a section here in the same
change that adds an image config.

Each file is the stored form of an image (what `phenix config get image/<name>`
prints). The `POSTBUILD_*` scripts in them are verbatim copies of the defaults
that `phenix image create` adds (`src/go/api/image/constants.go`), so the lint
hooks skip them. Only the `INSTALL_*` script of each image is ours.

All five images are Ubuntu 24.04 (noble), `minbase`, qcow2, built from the
`us.archive.ubuntu.com` mirror with the `main restricted universe multiverse`
components. They share this package set: `ca-certificates chrony curl dbus
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
  - No Wazuh agent yet (TODO: add it with the cm-wazuh manager version).
- **Build:** the commands above, with `<name>` set to `cm-svc`. inet-nuke
  uses a copy with protonuke added: copy `cm-svc.qc2` to
  `cm-svc-protonuke.qc2`, then run `phenix image inject-miniexe
  /usr/local/bin/protonuke <dir>/cm-svc-protonuke.qc2`.
- **Used by:** `ot-svc`, `bas-bbmd`, and (as `cm-svc-protonuke.qc2`)
  `inet-nuke`.
- **Pinned versions:** `bacpypes3==0.0.110` (the version of the BBMD spike),
  `pydantic>=2.7` (a floor, not a pin).

## Apps

phenix apps are not in this repository yet. When the `cherrymountain` app (and
any other app) is added under `apps/`, it ships its own `README.md` next to its
code, covering its metadata, stages, and tests. See the top-level
[README](../README.md).
