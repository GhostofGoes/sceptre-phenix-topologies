# netcheck

netcheck is a static check of the CherryMountain Brewery network. A static
check reads files and runs nothing. For the full and lite variants, netcheck
reads the topology (with `includeTopologies`), the scenario (with
`fromScenario`), and the workflow. For the ci slice, it reads these files only
for the address, workflow, boot-order, and inject checks.

The tool compares the files with DESIGN.md: the IP plan (section 10.2), the
conduit matrix (section 10.3), and the node inventory (section 11). A conduit
is a flow that the design allows. The tool needs no VM. It traces packets
through the static routes, NAT, IPsec, and the vrouter rulesets the way phenix
renders them.

```shell
python tools/netcheck/run_netcheck.py          # from cherrymountain-brewery/
python -m netcheck --variant lite              # from tools/netcheck/, or after pip install .
(cd tools/netcheck && python -m pytest)        # the tests, 35 seconds
```

A prek hook runs netcheck on every `prek run --all-files`, so CI fails on a
finding. When a check reports an error, the exit status is 1. The tool also
prints notes. An example of a note is a generated app block that plantgen does
not emit yet. Notes do not fail the run, and `-q` hides them.

## What it checks

| Check | What fails it |
|---|---|
| `plan` | One of these items differs from `netcheck/plan.py`: a node, image, resource, zone label, interface, address, mask, gateway, DNS server, route, autotunnel entry, or `delay.c2`. That file holds sections 10.2 and 11 as data. The totals are wrong. The totals of the full variant are 44 VMs, 68 vCPUs, and 160 GB. The totals of the lite variant are 37 VMs, 45 vCPUs, and 69 GB. A node has an `ntp-server` label. |
| `addresses` | An address occurs more than one time. An address is outside its VLAN. A gateway is not a router on the VLAN. Two VLAN subnets overlap. |
| `workflow` | The VLAN IDs differ from the plan. A VLAN is not pinned. A scenario annotation omits a topology that borrows from the scenario. The `defaultBridge` is not the bridge of the variant. The reason is that phenix lets only one stored experiment use a bridge other than `phenix`. Thus a shared bridge stops the creation of the second variant. The `mirrorBridge` is not the bridge of the workflow. |
| `boot-order` | A `delay.c2` waits for a node that boots later or for an unknown node. |
| `injects` | The source of an injection is not under `phenix-injects/`. |
| `router-commands` | No `commands` entry runs a script that phenix injects into a VyOS router. An entry runs the script from `/config/scripts/custom/` and not from `/usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/`. VyOS bind-mounts `/opt/vyatta/etc/config` over `/config`, and that mount hides the injected file. The permissions of the injected script are not `0755`. |
| `rulesets` | A firewall interface has no ruleset. A ruleset does not drop by default. A ruleset does not start with the stateful accept. A ruleset has a rule that is not a TCP or UDP accept with a destination address and port. |
| `flows` | There are about 150 conduit flows. The network must deliver each conduit flow. The reply must come back through the same firewalls. The flow must cross exactly the firewalls that its row names. Each of those firewalls must accept the flow with an explicit rule. For every other host pair and service, the network must drop the flow or have no route. There are tens of thousands of such combinations. |
| `rule-coverage` | A ruleset has an explicit rule that no conduit flow uses. |
| `nat-ipsec` | The SNAT, the DNAT, the IPsec sites and tunnels, or the netem interfaces differ from the plan. |
| `soh` | A conduit probe is missing from `testCustomReachability`. A TCP conduit has no probe. The rules block a probe. A blocked-path test is missing. A blocked-path test does not connect to its target, does not expect `DROPPED`, or tests a path that is open. |
| `scorch` | A run names a component that the scenario does not define. A run starts a `pcap` or `qos` component and never stops it. A `soh` component has an unknown check, or it asks for reachability tests without `network-config`. In the second case, phenix skips those tests. |
| `apps` | The app order is not ot-sim, cherrymountain, helics. A generated block names an unknown host or interface. The `ntp` host list is incomplete. A router is not `client: ntp`. |
| `app-hosts` | The WireGuard addresses, ports, or AllowedIPs are wrong. The mirror VLANs are wrong. |
| `services` | A `plant.hosts` entry differs from the plan. |

The conduit matrix is in `netcheck/conduits.py`. When a conduit changes, edit
the matrix together with the `vrouter` rulesets in `scenario-full.yml`. Then
`python -m netcheck` tells you what is missing or too wide.

## A site of the fleet

netcheck also checks one site of the fleet alone
([PLANS.md](../../docs/multisite/PLANS.md), sections 4.1 and 6). A site's
files are `phenix-configs/topology-site-<code>.yml`, which plantgen writes,
and `phenix-configs/scenario-site-<code>.yml`, which netcheck writes.

```shell
python tools/netcheck/run_netcheck.py --site ep        # one site
python tools/netcheck/run_netcheck.py --site all       # the four sites
python tools/netcheck/run_netcheck.py --write-sites    # write the site scenarios, then check
python tools/netcheck/run_netcheck.py                  # full, lite, ci, and the four sites
```

A site has no table of its own. `netcheck/site.py` derives its plan and its
conduits from two sources. The first source is the Front Range tables in
`plan.py` and `conduits.py`. The second source is the plant description of the
site, which plantgen builds from `plant/sites.yaml`.

- A node is the Front Range node with the name and the interfaces of the site.
- A conduit keeps the pairs of hosts that are both at the site.
- The firewalls that a pair crosses come from the networks and the routers of
  the site. A pair on one network crosses no firewall.
- The row `plc-peer` is the one fact that the matrix did not have. At the
  Front Range, the two PLCs share a network. At `ep`, each cell has its own
  control network, and the read crosses the OT firewall.

`netcheck/sitegen.py` writes the rulesets from these conduits. Each pair of an
enforced row gets one accept rule at each firewall on its path. A row from a
whole zone gets one rule with the subnet of the zone. Every ruleset drops by
default and starts with the stateful accept. The scenario also holds the SoH
probes of the conduits and the tests of the blocked paths.

| Site | Layout | Flows across a firewall | Rules |
|---|---|---:|---|
| `fr` | Two firewalls | 143 | `fr-fw-ot` 87, `fr-fw-edge` 0 |
| `ep` | Two firewalls, a control network for each cell | 105 | `ep-fw-ot` 82, `ep-fw-edge` 0 |
| `cs` | One firewall, three zones | 38 | `cs-fw-edge` 22 |
| `fc` | One firewall, two zones | 0 | `fc-fw-edge` 0 |

A site's run uses the check functions of a variant with the data of the site.
These checks run: `plan`, `addresses`, `boot-order`, `injects`,
`router-commands`, `rulesets`, `flows`, `rule-coverage`, and `soh`. One more
check, `generated`, fails when the committed scenario is not what the
generator writes.

A site's run leaves out these checks:

| Check | Why |
|---|---|
| `workflow` | A site has no workflow file yet |
| `nat-ipsec` | The WAN side of the edge is a later stage |
| `scorch` | The scenario of a site has no scorch app |
| `apps` | `sync.py` does not embed the app blocks of a site yet |
| `app-hosts` | WireGuard and the mirror are a later stage |
| `services` | `plant.hosts` is the file of the single site |

The rules cover only the conduits inside one site. A row with an end on the
internet, at the vendor, at the utility, or at a different site has no rule
yet. The run prints those rows in a `stage-4` note.

A flat network has no firewall inside it. At `cs` and `fc`, the SCADA host,
the HMIs, and the PLCs share one plant network. Hosts on one network reach
each other directly. Thus no rule can carry a conduit between them, and no
rule can stop a different flow.

The run of a site shows this in `separation` notes. The first note counts the
host pairs that share a zone network. The second note lists the conduit rows
that no firewall enforces there. The third note lists the paths that the Front
Range layout blocks and the site leaves open. At `fc`, 45 host pairs share a
network, and six such paths are open. These notes are not errors.

## What it does not check

- It models only the `forward` filter. That filter is all that the
  `vyatta.tmpl` of phenix renders. No filter applies to traffic to a router's
  own address, and the tool reports that traffic as `local`. That traffic is
  IKE, the routers' own syslog and NTP, and anything else on the routers'
  addresses.
- It does not test the flows among the hosts on the internet side (INET,
  VENDOR, UTIL). No plant firewall is between those hosts.
- It does not run VyOS, minimega, or any service. For the flows that send no
  reply (syslog, IKE and ESP), it does only the static check. It examines the
  WireGuard tunnel only through the configuration of the tunnel.
- It does not model the router configuration that `cm-vyos-config.sh` applies
  after boot. That configuration is drop logging and the syslog target, IKEv2,
  the NTP source of fw-ot, and the IPsec match on the utility DNP3 rule.

## Layout

- `netcheck/plan.py`: the VLAN and node tables.
- `netcheck/conduits.py`: the conduit matrix, SoH probes, and blocked paths.
- `netcheck/model.py`: loads the YAML as `phenix workflow apply` does.
- `netcheck/sim.py`: the packet-path simulator.
- `netcheck/checks.py`: the checks. `netcheck/cli.py`: the command line.
- `netcheck/reference.py`: the data that the checks compare a variant or a
  site with.
- `netcheck/site.py`: the plan and the conduits of a site, derived.
- `netcheck/sitegen.py`: the rulesets and the scenario of a site.
- `tests/`: the committed files must pass. Each test breaks a copy of the
  files and expects the finding. Examples of a break are a deleted rule, an
  extra rule, a missing route, and a duplicate address. `test_sync.py` and
  `test_services.py` cover `sync.py` and the BBMD service.
