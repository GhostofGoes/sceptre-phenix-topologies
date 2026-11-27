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
([PLANS.md](../../docs/multisite/PLANS.md), sections 4.1 and 6). A site has
three files that `phenix workflow apply` takes.

| File | Who writes it |
|---|---|
| `phenix-site-<code>.yml`, the workflow | plantgen |
| `phenix-configs/topology-site-<code>.yml` | plantgen |
| `phenix-configs/scenario-site-<code>.yml` | `sync.py` |

```shell
python tools/netcheck/run_netcheck.py --site ep        # one site
python tools/netcheck/run_netcheck.py --site all       # the four sites
python tools/netcheck/run_netcheck.py                  # full, lite, ci, and the four sites
```

netcheck writes no file. It computes two apps of the scenario, `vrouter` and
the network tests of `soh` (`netcheck/sitegen.py`). `sync.py` puts them into
the scenario with the app blocks that plantgen writes for the site. Thus one
tool writes the scenario. Run `python sync.py` after a change to the conduits
or to `plant/sites.yaml`.

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

`netcheck/sitegen.py` computes the rulesets from these conduits. Each pair of
an enforced row gets one accept rule at each firewall on its path. A row from
a whole zone gets one rule with the subnet of the zone. Every ruleset drops by
default and starts with the stateful accept. The `soh` app gets the SoH probes
of the conduits and the tests of the blocked paths.

| Site | Layout | Flows across a firewall | Rules |
|---|---|---:|---|
| `fr` | Two firewalls | 143 | `fr-fw-ot` 87, `fr-fw-edge` 0 |
| `ep` | Two firewalls, a control network for each cell | 105 | `ep-fw-ot` 82, `ep-fw-edge` 0 |
| `cs` | One firewall, three zones | 38 | `cs-fw-edge` 22 |
| `fc` | One firewall, two zones | 0 | `fc-fw-edge` 0 |

A site's run uses the check functions of a variant with the data of the site.
These checks run: `plan`, `addresses`, `workflow`, `boot-order`, `injects`,
`router-commands`, `rulesets`, `flows`, `rule-coverage`, `soh`, `apps`, and
`services`. One more check, `generated`, reads the committed scenario. It
fails when the `vrouter` app is not the computed one, or when the `soh` app
lacks a computed test.

Three checks are only for a site:

| Check | What it does |
|---|---|
| `servers` | Each node reaches the time server, the name server, and the syslog server that its files name. A server on a node of the site must pass the rules, there and back. For a server that the site does not have, the run prints a note |
| `layout` | The office host and each PLC are on different networks, and a firewall stops the office host on its way to a PLC. This is the floor of each site (PLANS.md 4.1) |
| `fleet/vlan-ids` | The workflows of the four sites and `phenix-wan.yml` are one set of IDs. An ID has one alias, an alias has one ID, and each ID is in 100 to 599 |

The sensor is a host that only a site has. It is a device of the SOC, so the
row from `zone:SOC` to `ot-svc` carries it (`SITE_ONLY_ZONES` in `site.py`).
At `ep`, the sensor then reaches its time server through `ep-fw-ot`.

Some checks have a part that is only for a site:

| Check | What it does for a site |
|---|---|
| `workflow` | It compares each VLAN ID with the plan of the fleet. The rule is the rule of plantgen (`plantgen/workflow.py`). The single-site IDs under it are those of `plan.py`, not those of `phenix.yml`. The bridge must be the bridge of the site |
| `injects` | Each injected file exists, also each copy under `phenix-injects/fleet/<code>/` |
| `plan` | It compares the VMs, the vCPUs, and the memory of the site with the table `TOTALS` in `site.py`. The source of that table is PLANS.md 4.1 |
| `apps` | The `cherrymountain` app names the site in `metadata.site`, and the scenario has no `helics` app. With that app, two brokers start on one host, and one of them fails. A time source can be `inet-traffic`, which no site has. The source must then carry the address that `topology-wan.yml` gives the host of the internet. Without the address, the ntp app of phenix uses the default source. Each other source must be a node of the site, with the name of the site |
| `services` | It compares the `plant.hosts` copy of the site with the nodes of the site. A node that injects a `plant.hosts` of a different plant is an error. A site with no `ot-svc` has no such file |

A site's run leaves out these checks:

| Check | Why |
|---|---|
| `nat-ipsec` | The WAN side of the edge is a later stage |
| `scorch` | The scenario of a site has no scorch app |
| `app-hosts` | WireGuard and the mirror are a later stage |

`tests/test_site_set.py` walks the whole set of a site: the workflow, the
topology file, the scenario, and each injected file. At `ep`, `cs`, and `fc`,
no file holds a Front Range address or the plain name of a host. The test has
a list of the exceptions, each with its reason. The walk also fails when a
file, an account, or a secret of the vendor reaches a site with no `remote`
module.

`generated`, `plan`, and `workflow` compare the files with what the generators
compute. A wrong rule in a generator passes them. Thus `tests/test_sites.py`
has a table that a person wrote, `EXPECTED_RULES`. For some destinations at
`ep` and `cs`, it lists each rule that opens the destination: the firewall,
the ruleset, and the source.

The rules of the matrix cover only the conduits inside one site. A row with
an end on the internet, at the vendor, at the utility, or at a different site
has no rule yet. The run prints those rows in a `stage-4` note.

`netcheck/fleet.py` has the first rows with an end at each of two sites: the
three conduits between a site's sensor and the one Malcolm (PLANS.md 4.6).

| Row | From | To | Port |
|---|---|---|---|
| `fleet-sensor-logs` | A sensor | `malcolm` | 5044/tcp |
| `fleet-sensor-sessions` | A sensor | `malcolm` | 9200/tcp |
| `fleet-sensor-pcap` | `malcolm` | A sensor | 8005/tcp |

An end is a role, and the plant description of a site says which host has it.
A site rules its own part of a row:

- Out of a site: each firewall between the host and the edge, and the edge.
  The rule is in the ruleset of the VLAN that the packet comes in from.
- Into a site: each firewall behind the edge. The edge takes the packet out
  of a tunnel. A tunnel is no interface of the topology, so phenix has no
  ruleset for it. That filter is the WAN generator's (stage 4).

| Site | Rules | Where |
|---|---|---|
| `fr` | 12 | For each of the three sensors: two in `fr-fw-ot` from the DMZ, and to the sensor one in `fr-fw-ot` from the SOC zone and one in `fr-fw-edge` from the DMZ |
| `ep` | 5 | Two in `ep-fw-ot` from the SOC zone, two in `ep-fw-edge` from the DMZ, and one in `ep-fw-ot` from the DMZ |
| `cs`, `fc` | 2 each | In the edge, from the plant network. The sensor is behind no other firewall, so no rule lets `malcolm` in |

`rule-coverage` accepts exactly these rules as rules that no conduit of the
site covers. Each must have one end outside the site. The `flows` sweep has
the three ports, so a rule that opens one of them inside a site is an error.
The run prints the rules in a `between-sites` note. No SoH test probes them,
because the far end is not in the experiment of a site.

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
- `netcheck/sitegen.py`: the rulesets and the SoH tests of a site.
- `tests/`: the committed files must pass. Each test breaks a copy of the
  files and expects the finding. Examples of a break are a deleted rule, an
  extra rule, a missing route, and a duplicate address. `test_sync.py` and
  `test_services.py` cover `sync.py` and the BBMD service.
