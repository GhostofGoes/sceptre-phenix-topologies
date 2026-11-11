# netcheck

A static check of the CherryMountain Brewery network. It reads the topology
(with `includeTopologies`), the scenario (with `fromScenario`), and the
workflow of the full and lite variants, and checks them against the DESIGN.md
IP plan (section 10.2), conduit matrix (section 10.3), and node inventory
(section 11). It needs no VM: it traces packets through the static routes, NAT,
IPsec, and the vrouter rulesets the way phenix renders them.

```shell
python tools/netcheck/run_netcheck.py          # from cherrymountain-brewery/
python -m netcheck --variant lite              # from tools/netcheck/, or after pip install .
(cd tools/netcheck && python -m pytest)        # the tests, 35 seconds
```

A prek hook runs it on every `prek run --all-files`, so CI fails on a finding.
The exit status is 1 when any check reports an error. Notes (for example, a
generated app block that plantgen has not emitted yet) are printed and do not
fail; `-q` hides them.

## What it checks

| Check | What fails it |
|---|---|
| `plan` | A node, image, resource, zone label, interface, address, mask, gateway, DNS server, route, autotunnel entry, or `delay.c2` that differs from `netcheck/plan.py`, which holds sections 10.2 and 11 as data; wrong totals (43 VMs, 67 vCPUs, 113 GB full; 37, 48, 71 lite); an `ntp-server` label |
| `addresses` | A duplicate address, an address outside its VLAN, a gateway that is not a router on the VLAN, overlapping VLAN subnets |
| `workflow` | VLAN IDs that differ from the plan, an unpinned VLAN, a scenario annotation that omits a topology that borrows from it, a `defaultBridge` that is not the variant's own (phenix lets only one stored experiment use a bridge other than `phenix`, so a shared bridge stops the second variant from being created), a `mirrorBridge` that is not the workflow's bridge |
| `boot-order` | A `delay.c2` on a node that boots later, or on an unknown node |
| `injects` | An injection whose source is not under `phenix-injects/` |
| `router-commands` | A script injected into a VyOS router that no `commands` entry runs, or that one runs from `/config/scripts/custom/` instead of `/usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/` (VyOS bind-mounts `/opt/vyatta/etc/config` over `/config`, which hides the injected file), or that is not injected `0755` |
| `rulesets` | A firewall interface without a ruleset, a ruleset that is not default drop, does not start with the stateful accept, or has a rule that is not a TCP or UDP accept with a destination address and port |
| `flows` | Each of the ~150 conduit flows must be delivered, its reply must come back through the same firewalls, it must cross exactly the firewalls its row names, and each of those must accept it with an explicit rule. Every other host pair and service (about 80,000) must be dropped or have no route |
| `rule-coverage` | An explicit rule that no conduit flow uses |
| `nat-ipsec` | The SNAT, DNAT, IPsec sites and tunnels, and netem interfaces that differ from the plan |
| `soh` | A conduit probe missing from `testCustomReachability`, a TCP conduit without a probe, a probe the rules block, a blocked-path test that is missing, does not connect to its target, does not expect `DROPPED`, or tests a path that is open |
| `scorch` | A run that names a component the scenario does not define, a `pcap` or `qos` component a run starts and never stops, a `soh` component with an unknown check, or one that asks for reachability tests without `network-config` (phenix skips them then) |
| `apps` | App order (ot-sim, cherrymountain, helics), unknown hosts or interfaces in generated blocks, an incomplete `ntp` host list, routers that are not `client: ntp` |
| `app-hosts` | The WireGuard addresses, ports, and AllowedIPs, and the mirror VLANs |
| `services` | `plant.hosts` entries that differ from the plan |

The conduit matrix is in `netcheck/conduits.py`. Edit it together with the
`vrouter` rulesets in `scenario-full.yml` when a conduit changes, and
`python -m netcheck` tells you what is missing or too wide.

## What it does not check

- It models the `forward` filter only, which is all phenix's `vyatta.tmpl`
  renders. Traffic to a router's own address (IKE, the routers' own syslog and
  NTP, anything on the routers' addresses) is unfiltered and reported as
  `local`.
- Flows among the internet-side hosts (INET, VENDOR, UTIL) are not swept: no
  plant firewall is between them.
- It does not run VyOS, minimega, or any service. Flows that send no reply
  (syslog, IKE and ESP) are checked statically only, and the WireGuard tunnel
  is checked only through its configuration.
- The router settings that `cm-vyos-config.sh` applies after boot (logging,
  IKEv2, NTP) are not modeled.

## Layout

- `netcheck/plan.py`: the VLAN and node tables.
- `netcheck/conduits.py`: the conduit matrix, SoH probes, and blocked paths.
- `netcheck/model.py`: loads the YAML like `phenix workflow apply`.
- `netcheck/sim.py`: the packet-path simulator.
- `netcheck/checks.py`: the checks. `netcheck/cli.py`: the command line.
- `tests/`: the committed files must pass, and each test breaks a copy
  (a deleted rule, an extra rule, a missing route, a duplicate address) and
  expects the finding. `test_sync.py` and `test_services.py` cover `sync.py` and
  the BBMD service.
