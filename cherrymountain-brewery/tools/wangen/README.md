# wangen

wangen reads `wan/wan.yaml`, the description of the mock internet and of the
company's WAN ([PLANS.md §4](../../docs/multisite/PLANS.md#4-the-choices)). The
tool uses Pydantic to make sure that the description is valid. Then it writes
the phenix files of the `wan` variant. A variant is one version of the
topology.

The outputs are committed files, and `--check` fails when they are stale. An
output is stale when a new generation gives a different file.

```shell
cd tools/wangen
python -m wangen            # write the three files
python -m wangen --check    # fail if a committed file is stale
python -m pytest            # unit and consistency tests
```

The tool needs Python 3.12 or later, `pydantic`, and `pyyaml`.

## Outputs

Paths are relative to `cherrymountain-brewery/`.

| File | What it holds |
|---|---|
| `phenix-configs/topology-wan.yml` | Ten VyOS routers and five hosts: 15 VMs, 25 vCPUs, and 25 GB. The interfaces of a router are in the order of its links in `wan.yaml` |
| `phenix-configs/scenario-wan.yml` | The `config` list of each router for the phenix `vrouter` app, the SoH (state of health) pings of each host, and one SCORCH run for each fault |
| `phenix-wan.yml` | The workflow, with a VLAN ID for each of the 18 aliases |

## What wan.yaml holds

| Key | What it holds |
|---|---|
| `routers` | One VyOS router for each entry, with its BGP AS and the prefixes that it announces. The routers are two of the transit carrier, four ISPs, the edge of the head office, and the edges of three sites |
| `links` | The point-to-point links, and how the first router sees the second: `customer`, `peer`, `provider`, `ibgp`, or `static` |
| `lans` | One LAN with one host behind each edge, and one host on the internet. The VLAN of a site LAN is a zone of that site in the plan of the fleet |
| `tunnels` | One tunnel from an uplink of a site to the head office. A site with two ISPs has two tunnels |
| `site_blocks` | The private address blocks of each site |
| `vpn` | The type of tunnel (`vti` for route-based IPsec, or `wireguard`), the public endpoint of the head office, and which end opens the IPsec tunnels |
| `head_office` | The edge router of the head office, its public prefix, and the address that its private addresses get on the internet |
| `timers` | The BGP keepalive and hold times, and the BFD interval |

`model.check` refuses an entry that does not fit the others, and the message
names the entry. For example, it refuses a tunnel through an ISP that has no
link to the site.

## What a configuration list holds

`wangen/vyos.py` writes one list of VyOS `set` commands for each router. phenix
puts the list into the script that the router runs at first boot. The lists
hold what the `vrouter` app does not write itself:

- An ISP or a transit router: BGP with the customer, peer, and provider rules.
  An ISP sends the head office a default route only while the ISP holds a
  prefix of its own providers.
- The head office: its public prefix to two ISPs, a default route from each,
  and a tunnel to each uplink of each site. It accepts from a site only the
  routes of that site. It drops and logs a packet from a tunnel whose source is
  not in the blocks of that site. Its private addresses go to the internet as
  one address of its own prefix, through either ISP.
- A site: a tunnel for each uplink, tied to that uplink, with BGP and BFD
  inside. The default route of each uplink stays only while the gateway of the
  uplink answers a ping. The private addresses of the site go to the internet
  as the address of the uplink.
- Each edge: discard routes, so that a private address never leaves an uplink
  outside a tunnel.

## What ran, and where

| What | Where it ran |
|---|---|
| The lists without the last four items (the source filter, NAT, the failover routes, and the conditional default route) | On four VyOS routers in CI, for both types of tunnel. A test makes sure that the generator writes the same lists as that spike ([spikes/wan-bgp](../../spikes/wan-bgp/README.md#the-same-wan-on-vyos-routers)) |
| The lists with those four items | The `wan-bgp-vyos` job commits them on the same four routers. [spikes/wan-bgp/README.md](../../spikes/wan-bgp/README.md) has the result of each run |
| The ten routers of the `wan` variant | Not run. The variant needs a phenix host or the cluster ([USER_GUIDE.md](../../USER_GUIDE.md#d-the-whole-wan-as-a-phenix-variant-wan)) |

The pre-shared keys in `scenario-wan.yml` come from a fixed text. They are
placeholders for a closed range, and they are not secrets.
