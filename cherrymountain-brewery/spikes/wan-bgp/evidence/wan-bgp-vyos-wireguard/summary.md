### Spike wan-bgp-vyos-wireguard: PASS

- PASS: phenix rendered the config list of shadecast after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/shadecast.boot)
- PASS: phenix rendered the config list of tumbleweed after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/tumbleweed.boot)
- PASS: phenix rendered the config list of fr-fw-edge after its own configuration, before the commit (118 commands; /phenix/experiments/spike-wan-bgp/vrouter/fr-fw-edge.boot)
- PASS: phenix rendered the config list of hc-fw-edge after its own configuration, before the commit (96 commands; /phenix/experiments/spike-wan-bgp/vrouter/hc-fw-edge.boot)
- PASS: shadecast committed the rendered configuration at first boot (20
Nov 23 01:08:35 vyos vyos-router[2670]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
Nov 23 01:09:00 shadecast commit[4647]: Successful change to active configurat)
- PASS: tumbleweed committed the rendered configuration at first boot (20
Nov 23 01:08:32 vyos vyos-router[2686]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
)
- PASS: fr-fw-edge committed the rendered configuration at first boot (44
Nov 23 01:08:39 vyos vyos-router[2666]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
Nov 23 01:09:06 fr-fw-edge commit[6145]: Successful change to active configura)
- PASS: hc-fw-edge committed the rendered configuration at first boot (21
Nov 23 01:08:15 vyos commit[2635]: Successful change to active configuration by user root on unknown
Nov 23 01:08:16 vyos vyos-router[2676]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: all 2 BGP sessions of shadecast are Established ({"198.18.0.18 (Tumbleweed Fiber)": "Established", "203.0.113.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 2 BGP sessions of tumbleweed are Established ({"198.18.0.17 (ShadeCast Cable)": "Established", "198.18.101.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 4 BGP sessions of fr-fw-edge are Established ({"10.255.0.1 (overlay hc-a)": "Established", "10.255.0.3 (overlay hc-b)": "Established", "198.18.101.1 (Tumbleweed Fiber)": "Established", "203.0.113.1 (ShadeCast Cable)": "Established"})
- PASS: all 2 BGP sessions of hc-fw-edge are Established ({"10.255.0.0 (overlay hc-a)": "Established", "10.255.0.2 (overlay hc-b)": "Established"})
- PASS: the head office installs a default route through each ISP, with its endpoint as the source (default nhid 34 proto bgp src 198.18.105.1 metric 20 nexthop via 198.18.101.1 dev eth1 weight 1 nexthop via 203.0.113.1 dev eth0 weight 1)
- PASS: the packets of hc-a leave through eth0, the uplink to shadecast (wants eth0 by the rule `fwmark 0x65`: 198.18.105.1 via 203.0.113.9 dev eth0 table 101 src 203.0.113.10 mark 0x65 uid 0 cache)
- PASS: the packets of hc-b leave through eth1, the uplink to tumbleweed (wants eth1 by the rule `fwmark 0x66`: 198.18.105.1 via 198.18.101.9 dev eth1 table 102 src 198.18.101.10 mark 0x66 uid 0 cache)
- PASS: both tunnels have a handshake, to the endpoint the site was given (endpoints {"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"}, handshakes [1795396155, 1795396156])
- PASS: the site reaches the head office's block through its primary tunnel (10.20.30.11 via 10.255.0.0 dev wg101 src 10.255.0.1 uid 0 cache 10.20.0.0/16 nhid 30 via 10.255.0.0 dev wg101 proto bgp metric 20)
- PASS: the head office reaches the site's block through the same tunnel (10.21.0.0/16 nhid 37 via 10.255.0.1 dev wg101 proto bgp metric 20)
- PASS: a host at the site reaches a host at the head office (--- 10.20.30.11 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 1808ms
rtt min/avg/max/mdev = 2.675/3.108/3.980/0.411 ms)
- PASS: the head office refuses another site's network when the site announces it (hc-fw-edge offered 192.168.20.0/24 on 2 tunnels: True (0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i *> 192.168.20.0/24 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office: % Net)
- PASS: the site moves to its second tunnel when its ShadeCast uplink goes silent (outage 2.4 s (12 of 300 pings lost; BFD detects in 3 s); now on wg102; --- 10.20.30.11 ping statistics --- 300 packets transmitted, 288 received, 4% packet loss, time 60106ms rtt min/avg/max/mdev = 2.)
- PASS: the site returns to its primary tunnel after the repair (the fault dropped 347 packets; outage on the way back 0.0 s (0 lost); now on wg101)
- PASS: both tunnels are as configured after the site's uplink failed and came back ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})
- PASS: the site keeps the head office when the head office loses its ShadeCast uplink (outage 2.6 s (13 of 300 lost); the site is on wg101; ShadeCast's route: 64501 64505 198.18.0.18 from 198.18.0.18 (198.18.101.254))
- PASS: both tunnels are as configured while the head office is on one ISP ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})
- PASS: the repair costs nothing, and the site is on its primary tunnel (the fault dropped 436 packets; outage 0.0 s (0 lost); now on wg101)
- PASS: with its uplink's link down, a tunnel does not move to the other uplink (0 of the first tunnel's packets left through eth1 in 20 s; its route lookup now answers `RTNETLINK answers: Invalid argument` (a blackhole route answers Invalid argument); traffic on wg102)
- PASS: the tunnel returns when the link does (wg101)
- PASS: after 200 s of silence on its uplink, the first tunnel comes back by itself (on wg101 8 s after the repair (the fault dropped 833 packets). The first tunnel before the repair: MjK527RQ8GquTIHN4xhOMBFeLjJl04bTkV6NZlTZ9QM= 1795396601 1795396838. After: MjK527RQ8GquTIHN4xhOMBFeLj)
- PASS: both tunnels are as configured after all the faults ({"wg102": "198.18.105.1:51822", "wg101": "198.18.105.1:51821"})

Versions:
- phenix: Branch afbcba7 [ghostofgoes/sceptre-phenix] (commit afbcba7) Mon Nov 23 01:04:53 UTC 2026
- minimega: ip-10-0-137-228: minimega 3.3.0 66ed1aacc8107f00cb003a512e835d6f32886dda 2026-11-23
- qemu: Copyright (c) 2003-2023 Fabrice Bellard and the QEMU Project developers
- ovs: DB Schema 8.5.1
- phenix-ref: afbcba70f488e222f0236114d162bbb526051784
- images-ref: f09368d753ef34177d192b51131b0d93b7b8e54b
