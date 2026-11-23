### Spike wan-bgp-vyos-wireguard: PASS

- PASS: phenix rendered the config list of shadecast after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/shadecast.boot)
- PASS: phenix rendered the config list of tumbleweed after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/tumbleweed.boot)
- PASS: phenix rendered the config list of fr-fw-edge after its own configuration, before the commit (164 commands; /phenix/experiments/spike-wan-bgp/vrouter/fr-fw-edge.boot)
- PASS: phenix rendered the config list of fc-fw-edge after its own configuration, before the commit (142 commands; /phenix/experiments/spike-wan-bgp/vrouter/fc-fw-edge.boot)
- PASS: shadecast committed the rendered configuration at first boot (20
Nov 23 07:36:35 vyos commit[2632]: Successful change to active configuration by user root on unknown
Nov 23 07:36:36 vyos vyos-router[2670]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: shadecast holds every line of its config list after the commit (all 65 lines are in `show configuration commands`)
- PASS: tumbleweed committed the rendered configuration at first boot (20
Nov 23 07:36:37 vyos commit[2632]: Successful change to active configuration by user root on unknown
Nov 23 07:36:38 vyos vyos-router[2673]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: tumbleweed holds every line of its config list after the commit (all 65 lines are in `show configuration commands`)
- PASS: fr-fw-edge committed the rendered configuration at first boot (44
Nov 23 07:36:22 vyos commit[2644]: Successful change to active configuration by user root on unknown
Nov 23 07:36:23 vyos vyos-router[2683]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fr-fw-edge holds every line of its config list after the commit (all 164 lines are in `show configuration commands`)
- PASS: fc-fw-edge committed the rendered configuration at first boot (21
Nov 23 07:36:26 vyos commit[2629]: Successful change to active configuration by user root on unknown
Nov 23 07:36:26 vyos vyos-router[2668]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fc-fw-edge holds every line of its config list after the commit (all 142 lines are in `show configuration commands`)
- PASS: all 2 BGP sessions of shadecast are Established ({"198.18.0.18 (Tumbleweed Fiber)": "Established", "203.0.113.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 2 BGP sessions of tumbleweed are Established ({"198.18.0.17 (ShadeCast Cable)": "Established", "198.18.101.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 4 BGP sessions of fr-fw-edge are Established ({"10.255.0.1 (overlay fc-a)": "Established", "10.255.0.3 (overlay fc-b)": "Established", "198.18.101.1 (Tumbleweed Fiber)": "Established", "203.0.113.1 (ShadeCast Cable)": "Established"})
- PASS: all 2 BGP sessions of fc-fw-edge are Established ({"10.255.0.0 (overlay fc-a)": "Established", "10.255.0.2 (overlay fc-b)": "Established"})
- PASS: the head office installs a default route through each ISP, with its endpoint as the source (default nhid 32 proto bgp src 198.18.105.1 metric 20 nexthop via 198.18.101.1 dev eth1 weight 1 nexthop via 203.0.113.1 dev eth0 weight 1)
- PASS: the packets of fc-a leave through eth0, the uplink to shadecast (wants eth0 by the rule `fwmark 0x65`: 198.18.105.1 via 203.0.113.9 dev eth0 table 101 src 203.0.113.10 mark 0x65 uid 0 cache)
- PASS: the packets of fc-b leave through eth1, the uplink to tumbleweed (wants eth1 by the rule `fwmark 0x66`: 198.18.105.1 via 198.18.101.9 dev eth1 table 102 src 198.18.101.10 mark 0x66 uid 0 cache)
- PASS: both tunnels have a handshake, to the endpoint the site was given (endpoints {"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"}, handshakes [1795419434, 1795419435])
- PASS: the site reaches the head office's block through its primary tunnel (10.20.30.11 via 10.255.0.0 dev wg101 src 10.21.10.1 uid 0 cache 10.20.0.0/16 nhid 30 via 10.255.0.0 dev wg101 proto bgp src 10.21.10.1 metric 20)
- PASS: the head office reaches the site's block through the same tunnel (10.21.0.0/16 nhid 36 via 10.255.0.1 dev wg101 proto bgp src 10.20.30.1 metric 20)
- PASS: each edge sends its own traffic into a tunnel from its LAN address (fc-fw-edge: 10.20.30.11 via 10.255.0.0 dev wg101 src 10.21.10.1 uid 0 cache; fr-fw-edge: 10.21.10.11 via 10.255.0.1 dev wg101 src 10.20.30.1 uid 0 cache)
- PASS: a 1420-byte packet with DF set crosses each tunnel, and a larger one is refused at the edge ({"fc-a": ["3 packets transmitted, 3 received, 0% packet loss, time 2003ms rtt min/avg/max/mdev = 1.214/1.308/1.401/0.076 ms", "PING 10.255.0.0 (10.255.0.0) 1393(1421) bytes of data. ping: local error:)
- PASS: a host at the site reaches a host at the head office (--- 10.20.30.11 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 1807ms
rtt min/avg/max/mdev = 2.284/2.887/4.121/0.540 ms)
- PASS: no edge would send an unused private address out of an uplink (16 probes; 10.0.0.0/8 is a blackhole at 2 of 2 edges)
- PASS: the head office refuses another site's network when the site announces it (fc-fw-edge offered 192.168.20.0/24 on 2 tunnels: True (0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i *> 192.168.20.0/24 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list)
- PASS: the head office refuses a more-specific of the head office's own block when the site announces it (fc-fw-edge offered 10.20.99.0/24 on 2 tunnels: True (0 0 0 i *> 10.20.99.0/24 0.0.0.0 0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list o)
- PASS: every BFD session is up at the configured interval before the faults (fr-fw-edge: 4 of 4 up; transmit intervals [1000] ms; fc-fw-edge: 2 of 2 up; transmit intervals [1000] ms)
- PASS: the site moves to its second tunnel when its ShadeCast uplink goes silent (outage 2.8 s (14 of 300 pings lost; BFD detects in 3 s); now on wg102; --- 10.20.30.11 ping statistics --- 300 packets transmitted, 286 received, 4.66667% packet loss, time 60163ms rtt min/avg/max/mde)
- PASS: the site returns to its primary tunnel after the repair (the fault dropped 397 packets; outage on the way back 0.0 s (0 lost); now on wg101)
- PASS: both tunnels are as configured after the site's uplink failed and came back ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})
- PASS: the site keeps the head office when the head office loses its ShadeCast uplink (outage 2.6 s (13 of 300 lost); the site stayed on wg101; ShadeCast's best path to the head office is '64501 64505')
- PASS: both tunnels are as configured while the head office is on one ISP ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})
- PASS: the repair costs nothing, and the site is on its primary tunnel (the fault dropped 409 packets; outage 0.0 s (0 lost); now on wg101)
- PASS: with its uplink's link down, a tunnel does not move to the other uplink (0 of the first tunnel's packets left through eth1 in 20 s; its route lookup now answers `RTNETLINK answers: Invalid argument` (a blackhole route answers Invalid argument); traffic on wg102)
- PASS: the tunnel returns when the link does (wg101)
- PASS: after 200 s of silence on its uplink, the first tunnel comes back by itself (on wg101 9 s after the repair (the fault dropped 834 packets). The first tunnel before the repair: B0R7HWhxGW8/VTUProsogaBlufIJpPVGNFlQxA7ptVI= 1795420045 1795420282. After: B0R7HWhxGW8/VTUProsogaBluf)
- PASS: both tunnels are as configured after all the faults ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})

Versions:
- phenix: Branch afbcba7 [ghostofgoes/sceptre-phenix] (commit afbcba7) Mon Nov 23 07:33:32 UTC 2026
- minimega: ip-10-0-129-10: minimega 3.3.0 66ed1aacc8107f00cb003a512e835d6f32886dda 2026-11-23
- qemu: Copyright (c) 2003-2023 Fabrice Bellard and the QEMU Project developers
- ovs: DB Schema 8.5.1
- phenix-ref: afbcba70f488e222f0236114d162bbb526051784
- images-ref: f09368d753ef34177d192b51131b0d93b7b8e54b
