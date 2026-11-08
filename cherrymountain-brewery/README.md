# CherryMountain Brewery

> **Status:** in progress. The design is complete, the tag database,
> generator, and cellar simulation exist, and the **ci variant** (the cellar
> slice, five VMs) is implemented but has not run on a phenix host yet. The
> full and lite variants are still design only. See
> [DESIGN.md](DESIGN.md#18-implementation-roadmap) for the phases.

The CherryMountain Brewery topology models a notional SCADA system for a
Colorado microbrewery and taproom. Its flagship West Coast IPA is "Fire of the
Phenix". It is an all open-source OT cyber range covering:

- **Process:** brewhouse, cellar, bottling and kegging.
- **Utilities:** glycol, steam, compressed air, CO₂, water treatment,
  wastewater neutralization, and clean-in-place (CIP).
- **Plant power:** an internal power-distribution model with PV, battery
  storage, and a standby generator.
- **Building automation:** a building automation system (BAS).
- **Security:** blue-team monitoring and a vendor remote-access path.

| Role | Component |
|---|---|
| SCADA and HMI | [FUXA](https://github.com/frangoteam/FUXA) |
| PLCs | [OpenPLC](https://github.com/Autonomy-Logic/openplc-runtime) |
| Field devices | [ot-sim](https://github.com/patsec/ot-sim) |
| Physics co-simulation | [HELICS](https://github.com/GMLC-TDC/HELICS) |
| Network monitoring and SIEM | [Malcolm](https://github.com/cisagov/Malcolm) and [Wazuh](https://github.com/wazuh/wazuh) |

See [DESIGN.md](DESIGN.md) for the full design: plant and process design,
power and building models, simulation, network architecture, phenix
integration, new software, testing, roadmap, risks, and references.

## What exists

| Path | Contents |
|---|---|
| `plant/` | `plant.yaml` tag database and the plantgen output under `plant/generated/<variant>/` |
| `tools/plantgen/` | Generator: tags, addresses, ot-sim and ntp app blocks, HELICS, OpenPLC, and FUXA inputs |
| `sim/` | `cherrymountain_sim`, the cellar HELICS federate |
| `phenix-ci.yml`, `phenix-configs/{topology,scenario}-ci.yml` | Workflow, topology, and scenario for the ci variant |
| `phenix-injects/` | Files the ci scenario injects: federate units, OpenPLC and FUXA provisioning, generated copies |
| `phenix-images/` | Image configs for `cm-sim`, `cm-otsim`, `cm-openplc`, `cm-fuxa` (noble), with [one README](phenix-images/README.md) that has a section per image |
| `plc/plc-cellar/` | The cellar PLC program in Structured Text, and `build_zip.sh`, which builds its OpenPLC v4 ZIP |
| `tests/integration/` | `ci_slice_docker.sh`: the cellar slice (ot-sim, OpenPLC v4.3.2, FUXA) in Docker, no KVM |
| `fuxa/` | The generated FUXA project for scada-fuxa |
| `tools/lint/` | Hook scripts for prek: systemd-analyze on units and shellcheck on shell in image YAML |
| `spikes/` | Phase 0 spike kits |
| `apps/` | Not created yet. Each phenix app will live here and ship its own `README.md` |

## The ci variant

Five VMs, 7 vCPUs, about 8 GB: sim-ctl, sim-process, rio-cellar, plc-cellar,
and scada-fuxa. It needs `/dev/kvm` on the host. The images and the PLC
program ZIP must exist first:

1. Build the four images from `phenix-images/` (each file's header has the
   commands), then add miniccc with `phenix image inject-miniexe`.
2. The OpenPLC program ZIP is `phenix-injects/openplc/plc-cellar.zip` (Git
   LFS), built headless by `plc/plc-cellar/build_zip.sh` (see
   [plc/plc-cellar/README.md](plc/plc-cellar/README.md)). The topology injects
   that path; rebuild it after any change to the plant or the program.
3. Apply the variant with its own workflow branch, from outside the directory:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-ci -c phenix-ci.yml /phenix/topologies/cherrymountain-brewery
```

Host access to FUXA is `http://<host>:11881` through the startup autotunnel.
The range admin password is in `phenix-injects/fuxa/fuxa.env` (range-only).

## Regenerate and test

Run these from this directory after changing `plant/plant.yaml`:

```shell
(cd tools/plantgen && python -m plantgen)          # rewrite plant/generated/*
python sync_ci.py                                    # copy generated files into phenix-injects/
python fuxa/build_project.py                         # rebuild the FUXA project
python sync_ci.py                                    # ... and copy it (run again after build_project.py)
plc/plc-cellar/build_zip.sh                          # rebuild the OpenPLC program ZIP (needs Node 22+)
```

Checks (all must pass; `prek run --all-files` covers lint and hygiene):

```shell
(cd tools/plantgen && python -m plantgen --check && python -m pytest)
(cd sim && python -m pytest)
python sync_ci.py --check                            # injects and scenario match plantgen output
python fuxa/build_project.py --check                 # FUXA project is current
python plc/plc-cellar/assemble.py                    # PLC program matches alarms.json and the I/O map
plc/plc-cellar/build_zip.sh --check                  # the committed ZIP is a fresh build (needs Node 22+)
(cd tests/integration && python -m pytest)           # ot-sim hand-render equals the phenix app
```

`assemble.py --iec2c <path>` also compiles the PLC program with matiec.

The integration test runs the slice in Docker (about 10 minutes the first time, for
the image builds; see the header of the script for its environment variables):

```shell
tests/integration/ci_slice_docker.sh
```

It starts ot-sim (`rio-cellar`) with the real `cherrymountain_sim` federate and a
HELICS broker, OpenPLC v4.3.2 running `plc-cellar.zip`, and FUXA 1.3.5 provisioned
with the generated project, then asserts the values flow ot-sim to PLC to FUXA,
that setpoints written through FUXA and Modbus land in the right `%MW` words and
move the valves, that the alarm bits read correctly, and that the spunding outputs
reach ot-sim. It also uploads two deliberately different `modbus_slave.json` files
to pin down how the v4 runtime clamps and segments the holding registers.

## Lint

`prek run --all-files` (also run by CI) checks this directory with:

- **phenix schemas:** every Topology, Scenario, Workflow, and Image config is
  validated with the sceptre-phenix JSON Schemas, fetched from a commit-pinned
  URL (`prek.toml` has the commit).
- **systemd:** `systemd-analyze verify` and a hardening (exposure) limit on
  every `.service` file and on the units that image scripts write.
- **shell:** shellcheck at style level on scripts, including extensionless
  ones, and on the shell embedded in `phenix-images/*.yml`.
- **YAML:** block style for phenix YAML. Only empty `{}` and `[]` may use flow
  syntax. JSON that phenix injects verbatim is exempt.
- **Docker and desktop files:** hadolint, dclint, and `desktop-file-validate`
  run when such files exist.

`spikes/` and `tests/` are exempt from the stricter checks. The system tools
that some hooks need are listed in
[CONTRIBUTING](../.github/CONTRIBUTING.md#code-quality).

## Applying the full design

The topology uses the `phenix workflow apply` directory layout
(`phenix.yml`, `phenix-configs/`, `phenix-injects/`). Once the other variants
exist, the full plant deploys with:

```shell
docker exec phenix phenix workflow apply /phenix/topologies/cherrymountain-brewery
```

The lite variant uses its own branch, like ci:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-lite -c phenix-lite.yml /phenix/topologies/cherrymountain-brewery
```
