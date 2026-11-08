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
| `phenix-images/` | Image configs for `cm-sim`, `cm-otsim`, `cm-openplc`, `cm-fuxa` (noble) |
| `plc/plc-cellar/` | The cellar PLC program in Structured Text, and how to build the Editor ZIP |
| `fuxa/` | The generated FUXA project for scada-fuxa |
| `spikes/` | Phase 0 spike kits |

## The ci variant

Five VMs, 7 vCPUs, about 8 GB: sim-ctl, sim-process, rio-cellar, plc-cellar,
and scada-fuxa. It needs `/dev/kvm` on the host. The images and the PLC
program ZIP must exist first:

1. Build the four images from `phenix-images/` (each file's header has the
   commands), then add miniccc with `phenix image inject-miniexe`.
2. Build the OpenPLC Editor ZIP as described in
   [plc/plc-cellar/README.md](plc/plc-cellar/README.md) and commit it as
   `phenix-injects/openplc/plc-cellar.zip` (Git LFS). The topology injects that
   path.
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
python fuxa/build_project.py                         # rebuild the FUXA project (then sync_ci.py)
```

Checks (all must pass; `prek run --all-files` covers lint and hygiene):

```shell
(cd tools/plantgen && python -m plantgen --check && python -m pytest)
(cd sim && python -m pytest)
python sync_ci.py --check                            # injects and scenario match plantgen output
python fuxa/build_project.py --check                 # FUXA project is current
python plc/plc-cellar/assemble.py                    # PLC program matches alarms.json and the I/O map
```

`assemble.py --iec2c <path>` also compiles the PLC program with matiec.

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
