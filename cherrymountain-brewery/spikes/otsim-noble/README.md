# Spike: ot-sim on Ubuntu noble

**Question.** Does `patsec/ot-sim` build and run on Ubuntu 24.04: a Python
HELICS federate, and the C++ DNP3 module in a master/outstation test?
**Decides:** `cm-otsim` on noble for everything, or a focal image for the two
DNP3 nodes (rtu-der, utility-derms) per DESIGN §12.3.

## Prerequisites

- Scratch Ubuntu 24.04 VM (2 vCPU, 4 GB) with internet access.
- A pinned ot-sim commit (`OTSIM_REF`). Upstream README says Ubuntu 20.04 is
  the last known-good DNP3 build and `pydnp3` fails on Bookworm.

## Steps

1. As root on noble: `OTSIM_REF=<commit> ./build_check.sh`. Read the summary
   table; keep `/var/tmp/otsim-noble-build.log`. Or build the image:
   `docker build -f Dockerfile.noble --build-arg OTSIM_REF=<commit> -t otsim-noble .`
   (the build fails when any step fails).
2. Python federate: `/opt/otsim-venv/bin/python helics_federate.py`
   (`helics` 3.6.1 is pulled in by `src/python`).
3. DNP3 outstation: `ot-sim-cpu-module fixture/dnp3-outstation.xml`, then
   `./dnp3_link_probe.py 127.0.0.1` (link addresses 1024/1 from the config).
4. Upstream end-to-end test (DNP3 class 0 scan, CROB trip, scan, CROB close,
   scan through a Modbus gateway into HELICS and OpenDSS): `./e2e_check.sh`.
   Upstream's `testing/dnp3/master.py` needs `pydnp3`, which does not build
   (see Result); the C++ `ot-sim-e2e-dnp3-master` (cmake `-DBUILD_E2E=ON`)
   replaces it, and is what upstream's own CI uses.
5. Modbus sanity (optional): the single-device example (`Procfile.single`)
   and `mbpoll`/`../openplc-v4/modbus_read.py` against it. Not run.
6. If steps 3 or 4 fail on noble, repeat on Ubuntu 20.04 (focal) to confirm
   the fallback, using `renewables/wind/turbine/phenix-images/ot-sim.yml` as
   the image recipe template. Not needed: they passed.

## Pass / fail

- [x] `build_check.sh` ends in PASS on noble (C++, Go, Python modules).
- [x] `helics_federate.py` PASS (value 42.5 delivered between federates).
- [x] DNP3 link probe PASS **and** the upstream master completes a Class 0
      scan and CROB against the outstation on noble.

First two pass, third fails: **go with focal DNP3 nodes** (record exact
failure). First or second fails: **no-go on noble**, use focal for all
ot-sim images and revise §12.3.

## Result

Run in Docker (`ubuntu:24.04`, 4 vCPU) on a no-KVM host.

- **Decision:** noble for all ot-sim nodes, DNP3 included, provided the
  pyzmq-from-source step is in the image recipe. No focal image needed.
  Status: pass (Docker, not a VM image; no systemd unit was tried).
- **ot-sim commit:** `9dc33047ff9fb474e61c8931c7dac7bf98df38ef` (main,
  2026-09-25; latest tag is v1.0.0). Submodules: opendnp3 3.1.2, cppzmq
  v4.8.1, fmt 8.1.1, json v3.10.5, sunspec models v2021-12. Toolchain: Ubuntu
  24.04.5 (`ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852`),
  gcc 13.3.0, cmake 3.28.3, golang-go 1.22.2, Python 3.12.3. Image
  `otsim-noble` id
  `sha256:d579cf33146688070e865359a9fc4395d1f42a9812a1a4ee5cbaaceeb7648370`,
  4.05 GB; `docker build -f Dockerfile.noble` took about 6 minutes of build
  plus image export. In this sandbox it was built on a base image that
  trusts the egress proxy CA (`--build-arg BASE=...`).
- **Evidence:**
  - Build summary: `apt-deps ok, submodules ok, cmake-cpp ok, go-modules ok,
    python-modules ok, pyzmq-system ok`, `pydnp3(info) FAIL`, `PASS: ot-sim
    builds on noble`. The C++ DNP3 module (opendnp3 3.1.2) builds on gcc 13.3
    without patches.
  - Installed binaries. `/usr/local/bin`: `ot-sim-cpu-module`,
    `ot-sim-message-bus`, `ot-sim-modbus-module`, `ot-sim-dnp3-module`,
    `ot-sim-logic-module`, `ot-sim-mqtt-module`, `ot-sim-node-red-module`,
    `ot-sim-sunspec-module`, `ot-sim-tailscale-module`, `ot-sim-telnet-module`,
    `ot-sim-e2e-dnp3-master` (only with `-DBUILD_E2E=ON`). Python console
    scripts land in the venv bin (`/opt/otsim-venv/bin`): `ot-sim-io-module`,
    `ot-sim-ground-truth-module`, `ot-sim-wind-turbine-*-module`. The old
    script's "absent: ot-sim-io-module" was a PATH false negative. In the tests the
    CPU module found every module through `PATH` (venv bin first), so a
    systemd unit should put both directories on `PATH`.
  - `helics_federate.py`: `helics version: 3.6.1 (2025-02-24)`, `received:
    {'v': 42.5, 't': 1.0}`, `PASS` (the script now calls
    `helicsFederateDisconnect`; `helicsFederateFinalize` is deprecated).
  - `dnp3_link_probe.py 127.0.0.1` against `fixture/dnp3-outstation.xml` ->
    `reply: 0564050b010000046440`, `PASS: LINK_STATUS received`. With the
    old default `--dest 10` there is no reply: the outstation answers only
    its configured `local-address` (1024); defaults now 1024/1.
  - `e2e_check.sh` (upstream e2e: HELICS broker, OpenDSS federate, device-1
    DNP3 outstation + Modbus master, device-2 Modbus outstation + HELICS I/O,
    C++ DNP3 master) in the built image: `closed: 1 / kW: 4.1`, trip command,
    `closed: 0 / kW: 0`, close command, `closed: 1 / kW: 2.56`, `PASS`.
    Master exit code 0.
- **Follow-ups:**
  - **The I/O module segfaults on noble with default pip wheels.** gdb:
    SIGSEGV in a `ZMQbg/IO` thread of pyzmq's bundled `libzmq-2f02442c.so.5.2.5`
    while HELICS's own bundled `libzmq.so.5.2.5` is loaded in the same process.
    Upstream's CI notes the same on ubuntu-latest ("I/O module segfaults ...",
    it runs in `debian:bookworm`). `pip install --force-reinstall --no-deps
    --no-binary pyzmq pyzmq` (links the system libzmq5; needs `libzmq3-dev`
    or `libzmq5-dev`) removes the crash; `build_check.sh` now does this. The
    CPU module restarts the dying I/O module, and the retry then fails with
    `duplicate federate name detected` at the HELICS broker, which hides the
    original crash.
  - `pydnp3` still does not build on noble: its source includes
    `<python2.7/Python.h>`. It is only for the legacy `testing/dnp3/master.py`.
  - `cm-otsim` must pin `opendssdirect.py~=0.8.4` for the OpenDSS federate
    (0.9.x: `Invalid terminal index (0)`); this affects only sim nodes that
    run OpenDSS.
  - `testing/e2e/helics/data/IEEE13/*.csv` are Git LFS objects. A clone
    without LFS has pointers and the load shape is empty (every kW 0, the
    master fails its `kW` range check). `e2e_check.sh` substitutes a synthetic
    load shape when it sees a pointer. This sandbox could not fetch LFS.
  - The Go build needs network: `go-jsonschema@v0.24.1` requires Go 1.25, so
    the 1.22.2 toolchain downloads go1.26.10 (observed in the build log). For
    an offline image build, vendor or pre-seed that toolchain.
  - The C++ DNP3 module rejects an XML comment placed before the root
    `<ot-sim>` element ("missing root 'ot-sim' element").
  - Not run: the Modbus single-device example, ot-sim under systemd, the
    focal fallback, a BACnet module (does not exist).
