# wan-bgp-netns

| Check | Result | Detail |
|---|---|---|
| every BGP session is Established | pass | 13 sessions in 8 s |
| ShadeCast reaches Tumbleweed and its customer Marmot over the peering link, not through transit | pass | '64501', '64501 64502' |
| a route learned from a peer is not given to the provider (no valley) | pass | cb-west to Marmot's prefix: '64501 64502' |
| no ISP carries a private route | pass | {} |
| the head office installs a default route through each of its two ISPs | pass | default via fr-sc, fr-tw |
| neither default route of the head office is marked stale | pass | 2 paths; stale from no ISP |
| a site learns only site prefixes over the tunnels (no default, no public route) | pass | {"hc-fw-edge": ["10.20.30.0/24", "10.22.30.0/24", "192.168.20.0/24"], "ws-fw-edge": ["10.20.30.0/24", "10.21.10.0/24", "192.168.20.0/24"], "gc-fw-edge": ["10.20.30.0/24", "10.21.10.0/24", "10.22.30.0/24"]} |
| every site reaches the head office and every other site | pass | {"hc-historian to fr-historian": true, "hc-historian to ws-historian": true, "hc-historian to gc-historian": true, "ws-historian to fr-historian": true, "ws-historian to hc-historian": true, "ws-historian to gc-historian": true, "gc-historian to fr-historian": true, "gc-historian to hc-historian": true, "gc-historian to ws-historian": true} |
| site traffic rides the primary tunnel | pass | hc-fw-edge via wgs-hc-a, fr-fw-edge back via wgh-hc-a |
| a private address is not reachable from the internet side | pass | inet cannot ping fr-historian |
| the head office's public endpoint answers from the internet | pass | inet pings 198.18.105.1 |
| the High Country site fails over to its second ISP when its ShadeCast uplink goes silent | pass | outage 0.8 s, now via wgs-hc-b |
| the other sites do not notice | pass | ws-historian to fr-historian: 0 lost, gc-historian to fr-historian: 0 lost |
| the High Country site returns to the primary tunnel after the repair | pass | via wgs-hc-a, outage on the way back 0.0 s |
| every site keeps the head office when it loses one of its two ISPs | pass | longest outage 0.8 s; ShadeCast now reaches the head office over '64501 64505' |
| the fault was real: ShadeCast dropped packets on the head office's link and rerouted through its peer | pass | 117 packets dropped; High Country underlay 203.0.113.9 > 198.18.105.1 -> 203.0.113.9 > 198.18.0.18 > 198.18.105.1 |
| every site keeps the head office through the loss of a whole ISP | pass | longest outage 0.8 s; High Country via wgs-hc-b, Western Slope via wgs-ws-a |
| the outage is bounded by the BGP hold time of the ISP core | pass | 0.8 s against a 9 s hold time |
| a site whose path crosses the dead ISP in the core waits for the BGP hold timer | pass | cb-west reached the head office over '64500 64505'; Gulf Coast lost 9.8 s against a 9 s hold time; underlay 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 -> 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 |
| with its uplink's link down, a tunnel does not move to the other uplink | pass | lookup for hc-a: no route (blackhole); traffic via wgs-hc-b |
| the tunnel returns when the link does | pass | after 2 s |
| no tunnel endpoint has moved off the address its site was given | pass | all 5 as configured |
| every tunnel still leaves through its own uplink | pass | {"hc-a": "hc-sc", "hc-b": "hc-mm", "ws-a": "ws-tw", "ws-b": "ws-sc", "gc-a": "gc-pc"} |
| the lab converges again after the repairs | pass | 0 s |
| the head office refuses another site's network when a site announces it | pass | hc-fw-edge offered 192.168.20.0/24: True; the head office holds it from 10.255.0.9 (the Gulf Coast tunnel is 10.255.0.9), next hop wgh-gc-a; ws-historian still reaches gc-historian |
| an origin hijack by Pelican takes the transit carrier's traffic and cuts off the site behind Pelican | pass | paths {"cb-west": "64503", "cb-south": "64503", "shadecast": "64505", "tumbleweed": "64505"}; reach {"hc-historian": true, "ws-historian": true, "gc-historian": false} |
| the head office's own ISPs still prefer their customer route, so their sites stay up | pass | shadecast '64505', tumbleweed '64505' |
| the site behind Pelican comes back when the announcement is withdrawn | pass | after 3 s; cb-south path '64501 64505' |

Each site pings the head office's host ten times a second through the event. The tunnel
path is the hops inside the VPN; the underlay path is the internet hops under the site's
primary tunnel.

| Event | Stream | Sent | Lost | Longest gap (s) | Tunnel path before | Tunnel path after | Underlay before | Underlay after | Fault drops |
|---|---|---:|---:|---:|---|---|---|---|---:|
| High Country uplink to ShadeCast, silent | hc-historian to fr-historian | 250 | 8 | 0.8 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 107 |
| High Country uplink to ShadeCast, silent | ws-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 107 |
| High Country uplink to ShadeCast, silent | gc-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 107 |
| High Country uplink to ShadeCast, restored | hc-historian to fr-historian | 250 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| High Country uplink to ShadeCast, restored | ws-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| High Country uplink to ShadeCast, restored | gc-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, silent | hc-historian to fr-historian | 250 | 8 | 0.8 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | 203.0.113.9 > 198.18.0.18 > 198.18.105.1 | 117 |
| head office uplink to ShadeCast, silent | ws-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 117 |
| head office uplink to ShadeCast, silent | gc-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 117 |
| head office uplink to ShadeCast, restored | hc-historian to fr-historian | 250 | 0 | 0.0 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 203.0.113.9 > 198.18.0.18 > 198.18.105.1 | 203.0.113.9 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, restored | ws-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, restored | gc-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| ShadeCast outage, silent | hc-historian to fr-historian | 350 | 8 | 0.8 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 369 |
| ShadeCast outage, silent | ws-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 369 |
| ShadeCast outage, silent | gc-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 369 |
| ShadeCast outage, restored | hc-historian to fr-historian | 350 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored | ws-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored | gc-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| ShadeCast outage, the transit carrier routing through it | hc-historian to fr-historian | 450 | 9 | 0.9 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 517 |
| ShadeCast outage, the transit carrier routing through it | ws-historian to fr-historian | 450 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 517 |
| ShadeCast outage, the transit carrier routing through it | gc-historian to fr-historian | 450 | 98 | 9.8 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 517 |
| ShadeCast outage, restored again | hc-historian to fr-historian | 350 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored again | ws-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored again | gc-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 | 0 |

| Fact | Value |
|---|---|
| wireguard | userspace |
| AS path, cb-south to the head office's prefix | 64501 64505 |
| AS path, pelican to the head office's prefix | 64496 64501 64505 |
| AS path, marmot to ShadeCast's prefix | 64501 64500 |
| AS path, shadecast to Tumbleweed's prefix | 64501 |
| AS path, shadecast to Marmot's prefix | 64501 64502 |
| AS path, tumbleweed to Pelican's prefix | 64496 64503 |
| path, hc-historian to fr-historian | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 |
| path, ws-historian to fr-historian | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 |
| path, gc-historian to fr-historian | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 |
| path, hc-historian to gc-historian | 10.21.10.1 > 10.255.0.0 > 10.255.0.9 > 192.168.20.11 |
| pin | mark |
| tunnel endpoints at the start | {"hc-a": "198.18.105.1:51821", "hc-b": "198.18.105.1:51822", "ws-a": "198.18.105.1:51823", "ws-b": "198.18.105.1:51824", "gc-a": "198.18.105.1:51825"} |
| routing daemons, resident MiB | {"bfdd": 68.7, "bgpd": 174.2, "staticd": 60.1, "wireguard-go": 64.7, "zebra": 134.2} |
| routing daemons, total MiB | 501.9 |
| path, hc-historian to fr-historian, head office on one ISP | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 |
| tunnel endpoints after the faults and repairs | {"hc-a": "198.18.105.1:51821", "hc-b": "198.18.105.1:51822", "ws-a": "198.18.105.1:51823", "ws-b": "198.18.105.1:51824", "gc-a": "198.18.105.1:51825"} |
| foreign prefix, the head office's paths before | ["10.255.0.9"] |
| foreign prefix, the head office's paths after | ["10.255.0.9"] |
| hijack, AS path to the head office's prefix before | {"cb-west": "64500 64505", "cb-south": "64500 64505", "shadecast": "64505", "tumbleweed": "64505"} |
| hijack, AS path to the head office's prefix after | {"cb-west": "64503", "cb-south": "64503", "shadecast": "64505", "tumbleweed": "64505"} |
