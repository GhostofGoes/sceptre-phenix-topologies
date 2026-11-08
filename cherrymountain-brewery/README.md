# CherryMountain Brewery

> **Status:** design. This directory currently contains the design only. The
> configs, images, PLC programs, and simulation code are built in the phases
> described in [DESIGN.md](DESIGN.md#18-implementation-roadmap).

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

## Planned usage

The topology uses the `phenix workflow apply` directory layout
(`phenix.yml`, `phenix-configs/`, `phenix-injects/`). Once implemented, it is
deployed with:

```shell
docker exec phenix phenix workflow apply /phenix/topologies/cherrymountain-brewery
```

The lite and CI variants apply with their own workflow branch:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-lite -c phenix-lite.yml /phenix/topologies/cherrymountain-brewery
```
