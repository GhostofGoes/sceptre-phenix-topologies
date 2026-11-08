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
   table; keep `/var/tmp/otsim-noble-build.log`.
2. Python federate: `/opt/otsim-venv/bin/pip install helics` (if not already
   pulled in) then `/opt/otsim-venv/bin/python helics_federate.py`.
3. Modbus sanity: run the upstream single-device example (`Procfile.single`:
   broker, OpenDSS federate, `ot-sim-cpu-module config/single-device/device.xml`)
   and `mbpoll`/`../openplc-v4/modbus_read.py` against it.
4. DNP3 outstation: start the DNP3 module in outstation mode from the repo's
   example config, then `./dnp3_link_probe.py 127.0.0.1` (TODO(verify):
   link addresses from the example config).
5. DNP3 master/outstation test: run the upstream test master (README: `cd
   testing/dnp3 && python3 master.py`, a Class 0 scan, a CROB, another scan).
   TODO(verify): upstream also references `testing/e2e/helics`; use whichever
   path exists at your pinned commit. Needs `pydnp3` from step 1.
6. If steps 4 or 5 fail on noble, repeat 1-5 on Ubuntu 20.04 (focal) to
   confirm the fallback, using `renewables/wind/turbine/phenix-images/ot-sim.yml`
   as the image recipe template.
7. Optionally build the repeatable image: `docker build -f Dockerfile.noble .`.

## Pass / fail

- [ ] `build_check.sh` ends in PASS on noble (C++, Go, Python modules).
- [ ] `helics_federate.py` PASS (value 42.5 delivered between federates).
- [ ] DNP3 link probe PASS **and** the upstream master completes a Class 0
      scan and CROB against the outstation on noble.

First two pass, third fails: **go with focal DNP3 nodes** (record exact
failure). First or second fails: **no-go on noble**, use focal for all
ot-sim images and revise §12.3.

## Result

- **Decision:** noble for all / noble + focal DNP3 nodes / focal for all
- **ot-sim commit:**
- **Evidence:** (build summary table, helics output, master.py output)
- **Follow-ups:** (patches needed for pydnp3, installed binary names for the systemd unit)
