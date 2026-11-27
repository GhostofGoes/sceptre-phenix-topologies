# wan-bgp-netns

| Check | Result | Detail |
|---|---|---|
| every BGP session is Established | pass | 13 sessions in 8 s |
| ShadeCast reaches Tumbleweed and its customer Marmot over the peering link, not through transit | pass | '64501' at local preference 150, '64501 64502' |
| a route learned from a peer is not given to the provider (no valley) | pass | cb-west holds Marmot's prefix from 198.18.0.10 (Tumbleweed is 198.18.0.10; a path from ShadeCast, 198.18.0.6, would be a leak) |
| an ISP refuses a private prefix and one longer than a /24 from a customer, and carries no private route | pass | Marmot offered 10.99.0.0/24, 198.18.102.0/25; Tumbleweed holds {"10.99.0.0/24": [], "198.18.102.0/25": []}; private routes at the ISPs: {} |
| the head office installs a default route through each of its two ISPs | pass | default via fr-sc, fr-tw |
| neither default route of the head office is marked stale | pass | 2 paths; stale from no ISP |
| a site learns only site prefixes over the tunnels (no default, no public route) | pass | {"fc-fw-edge": ["10.20.30.0/24", "10.22.30.0/24", "192.168.20.0/24"], "ep-fw-edge": ["10.20.30.0/24", "10.21.10.0/24", "192.168.20.0/24"], "cs-fw-edge": ["10.20.30.0/24", "10.21.10.0/24", "10.22.30.0/24"]} |
| every site reaches the head office and every other site | pass | {"fc-historian to fr-historian": true, "fc-historian to ep-historian": true, "fc-historian to cs-historian": true, "ep-historian to fr-historian": true, "ep-historian to fc-historian": true, "ep-historian to cs-historian": true, "cs-historian to fr-historian": true, "cs-historian to fc-historian": true, "cs-historian to ep-historian": true} |
| site traffic rides the primary tunnel | pass | fc-fw-edge via wgs-fc-a, fr-fw-edge back via wgh-fc-a |
| no edge would send an unused private address out of an uplink | pass | 25 probes at 4 edges; 10.0.0.0/8 is a blackhole at 4 of them |
| a private address is not reachable from the internet side | pass | inet cannot ping fr-historian |
| the head office's public endpoint answers from the internet | pass | inet pings 198.18.105.1 |
| the Fort Collins site fails over to its second ISP when its ShadeCast uplink goes silent | pass | outage 0.9 s, now via wgs-fc-b |
| the other sites do not notice | pass | ep-historian to fr-historian: 0 lost, cs-historian to fr-historian: 0 lost |
| the Fort Collins site returns to the primary tunnel after the repair | pass | via wgs-fc-a, outage on the way back 0.0 s |
| every site keeps the head office when it loses one of its two ISPs | pass | started converged on fc-a: True; Fort Collins lost 0.6 s, the longest outage was 0.6 s; ShadeCast now reaches the head office over '64501 64505' |
| the fault was real: Fort Collins's tunnel crossed the failed link, and ShadeCast rerouted through its peer | pass | Fort Collins underlay 203.0.113.9 > 198.18.105.1 -> 203.0.113.9 > 198.18.0.18 > 198.18.105.1; 110 packets dropped |
| every site keeps the head office through the loss of a whole ISP | pass | longest outage 1.0 s; Fort Collins via wgs-fc-b, Eastern Plains via wgs-ep-a |
| the outage is bounded by the BGP hold time of the ISP core | pass | 1.0 s against a 9 s hold time |
| a site whose path crosses the dead ISP in the core waits for the BGP hold timer | pass | cb-west reached the head office over '64500 64505'; Colorado Springs lost 9.0 s against a 9 s hold time; underlay 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 -> 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 |
| with its uplink's link down, a tunnel does not move to the other uplink | pass | lookup for fc-a: RTNETLINK answers: Invalid argument; table 101: blackhole default proto static metric 20; traffic via wgs-fc-b |
| the tunnel returns when the link does | pass | after 2 s |
| no tunnel endpoint has moved off the address its site was given | pass | all 5 as configured |
| every tunnel still leaves through its own uplink | pass | {"fc-a": "fc-sc", "fc-b": "fc-mm", "ep-a": "ep-tw", "ep-b": "ep-sc", "cs-a": "cs-bc"} |
| the lab converges again after the repairs | pass | 0 s |
| the head office refuses another site's network when a site announces it | pass | fc-fw-edge offered 192.168.20.0/24, 10.20.99.0/24; the head office holds 192.168.20.0/24 from 10.255.0.9 (the Colorado Springs tunnel is 10.255.0.9), next hop wgh-cs-a; ep-historian still reaches cs-historian |
| the head office refuses a site's more-specific of the head office's own block | pass | fc-fw-edge offered 10.20.99.0/24: True; the head office holds it from no neighbor |
| an origin hijack by Bristlecone takes the transit carrier's traffic and cuts off the site behind Bristlecone | pass | paths {"cb-west": "64503", "cb-south": "64503", "shadecast": "64505", "tumbleweed": "64505"}; reach {"fc-historian": true, "ep-historian": true, "cs-historian": false} |
| the head office's own ISPs still prefer their customer route, so their sites stay up | pass | shadecast '64505', tumbleweed '64505' |
| the site behind Bristlecone comes back when the announcement is withdrawn | pass | after 2 s; cb-south path '64501 64505' |

Each site pings the head office's host ten times a second through the event. The tunnel
path is the hops inside the VPN; the underlay path is the internet hops under the site's
primary tunnel.

| Event | Stream | Sent | Lost | Longest gap (s) | Tunnel path before | Tunnel path after | Underlay before | Underlay after | Fault drops |
|---|---|---:|---:|---:|---|---|---|---|---:|
| Fort Collins uplink to ShadeCast, silent | fc-historian to fr-historian | 250 | 9 | 0.9 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 107 |
| Fort Collins uplink to ShadeCast, silent | ep-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 107 |
| Fort Collins uplink to ShadeCast, silent | cs-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 107 |
| Fort Collins uplink to ShadeCast, restored | fc-historian to fr-historian | 250 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| Fort Collins uplink to ShadeCast, restored | ep-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| Fort Collins uplink to ShadeCast, restored | cs-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, silent | fc-historian to fr-historian | 250 | 6 | 0.6 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | 203.0.113.9 > 198.18.0.18 > 198.18.105.1 | 110 |
| head office uplink to ShadeCast, silent | ep-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 110 |
| head office uplink to ShadeCast, silent | cs-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 110 |
| head office uplink to ShadeCast, restored | fc-historian to fr-historian | 250 | 0 | 0.0 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 203.0.113.9 > 198.18.0.18 > 198.18.105.1 | 203.0.113.9 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, restored | ep-historian to fr-historian | 250 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| head office uplink to ShadeCast, restored | cs-historian to fr-historian | 250 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| ShadeCast outage, silent | fc-historian to fr-historian | 350 | 10 | 1.0 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 355 |
| ShadeCast outage, silent | ep-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 355 |
| ShadeCast outage, silent | cs-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 355 |
| ShadeCast outage, restored | fc-historian to fr-historian | 350 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored | ep-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored | cs-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 0 |
| ShadeCast outage, the transit carrier routing through it | fc-historian to fr-historian | 450 | 10 | 1.0 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 203.0.113.9 > 198.18.105.1 | * > * > * > * > * > * > * > * > * > * > * > * | 520 |
| ShadeCast outage, the transit carrier routing through it | ep-historian to fr-historian | 450 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 520 |
| ShadeCast outage, the transit carrier routing through it | cs-historian to fr-historian | 450 | 90 | 9.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 520 |
| ShadeCast outage, restored again | fc-historian to fr-historian | 350 | 0 | 0.0 | 10.21.10.1 > 10.255.0.2 > 10.20.30.11 | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 | * > * > * > * > * > * > * > * > * > * > * > * | 203.0.113.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored again | ep-historian to fr-historian | 350 | 0 | 0.0 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 | 198.18.101.9 > 198.18.105.1 | 198.18.101.9 > 198.18.105.1 | 0 |
| ShadeCast outage, restored again | cs-historian to fr-historian | 350 | 0 | 0.0 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.10 > 198.18.105.1 | 198.18.103.1 > 198.18.0.13 > 198.18.0.1 > 198.18.0.6 > 198.18.105.1 | 0 |

| Fact | Value |
|---|---|
| wireguard | userspace |
| AS path, cb-south to the head office's prefix | 64501 64505 |
| AS path, bristlecone to the head office's prefix | 64496 64501 64505 |
| AS path, marmot to ShadeCast's prefix | 64501 64500 |
| AS path, shadecast to Tumbleweed's prefix | 64501 |
| AS path, shadecast to Marmot's prefix | 64501 64502 |
| AS path, tumbleweed to Bristlecone's prefix | 64496 64503 |
| path, fc-historian to fr-historian | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 |
| path, ep-historian to fr-historian | 10.22.30.1 > 10.255.0.4 > 10.20.30.11 |
| path, cs-historian to fr-historian | 192.168.20.1 > 10.255.0.8 > 10.20.30.11 |
| path, fc-historian to cs-historian | 10.21.10.1 > 10.255.0.0 > 10.255.0.9 > 192.168.20.11 |
| pin | mark |
| tunnel endpoints at the start | {"fc-a": "198.18.105.1:51821", "fc-b": "198.18.105.1:51822", "ep-a": "198.18.105.1:51823", "ep-b": "198.18.105.1:51824", "cs-a": "198.18.105.1:51825"} |
| routing daemons, proportional (PSS) MiB | {"bfdd": 29.7, "bgpd": 122.2, "staticd": 26.6, "wireguard-go": 26.7, "zebra": 91.3} |
| routing daemons, total MiB | 296.5 |
| path, fc-historian to fr-historian, head office on one ISP | 10.21.10.1 > 10.255.0.0 > 10.20.30.11 |
| tunnel endpoints after the faults and repairs | {"fc-a": "198.18.105.1:51821", "fc-b": "198.18.105.1:51822", "ep-a": "198.18.105.1:51823", "ep-b": "198.18.105.1:51824", "cs-a": "198.18.105.1:51825"} |
| foreign prefix, the head office's paths before | ["10.255.0.9"] |
| foreign prefix, the head office's paths after | ["10.255.0.9"] |
| hijack, AS path to the head office's prefix before | {"cb-west": "64500 64505", "cb-south": "64500 64505", "shadecast": "64505", "tumbleweed": "64505"} |
| hijack, AS path to the head office's prefix after | {"cb-west": "64503", "cb-south": "64503", "shadecast": "64505", "tumbleweed": "64505"} |
