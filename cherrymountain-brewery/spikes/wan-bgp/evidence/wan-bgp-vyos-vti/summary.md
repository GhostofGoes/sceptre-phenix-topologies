### Spike wan-bgp-vyos-vti: PASS

- PASS: phenix rendered the config list of shadecast after its own configuration, before the commit (59 commands; /phenix/experiments/spike-wan-bgp/vrouter/shadecast.boot)
- PASS: phenix rendered the config list of tumbleweed after its own configuration, before the commit (59 commands; /phenix/experiments/spike-wan-bgp/vrouter/tumbleweed.boot)
- PASS: phenix rendered the config list of fr-fw-edge after its own configuration, before the commit (119 commands; /phenix/experiments/spike-wan-bgp/vrouter/fr-fw-edge.boot)
- PASS: phenix rendered the config list of hc-fw-edge after its own configuration, before the commit (93 commands; /phenix/experiments/spike-wan-bgp/vrouter/hc-fw-edge.boot)
- PASS: shadecast committed the rendered configuration at first boot (20
Nov 22 23:51:47 vyos vyos-router[2690]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
Nov 22 23:52:14 shadecast commit[4533]: Successful change to active configurat)
- PASS: tumbleweed committed the rendered configuration at first boot (20
Nov 22 23:51:51 vyos commit[2623]: Successful change to active configuration by user root on unknown
Nov 22 23:51:52 vyos vyos-router[2662]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fr-fw-edge committed the rendered configuration at first boot (44
Nov 22 23:51:42 vyos commit[2634]: Successful change to active configuration by user root on unknown
Nov 22 23:51:42 vyos vyos-router[2671]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: hc-fw-edge committed the rendered configuration at first boot (23
Nov 22 23:51:51 vyos commit[2638]: Successful change to active configuration by user root on unknown
Nov 22 23:51:52 vyos vyos-router[2678]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: all 2 BGP sessions of shadecast are Established ({"198.18.0.18 (Tumbleweed Fiber)": "Established", "203.0.113.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 2 BGP sessions of tumbleweed are Established ({"198.18.0.17 (ShadeCast Cable)": "Established", "198.18.101.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 4 BGP sessions of fr-fw-edge are Established ({"10.255.0.1 (overlay hc-a)": "Established", "10.255.0.3 (overlay hc-b)": "Established", "198.18.101.1 (Tumbleweed Fiber)": "Established", "203.0.113.1 (ShadeCast Cable)": "Established"})
- PASS: all 2 BGP sessions of hc-fw-edge are Established ({"10.255.0.0 (overlay hc-a)": "Established", "10.255.0.2 (overlay hc-b)": "Established"})
- PASS: the head office installs a default route through each ISP, with its endpoint as the source (default nhid 26 proto bgp src 198.18.105.1 metric 20 nexthop via 203.0.113.1 dev eth0 weight 1 nexthop via 198.18.101.1 dev eth1 weight 1)
- PASS: the packets of hc-a leave through eth0, the uplink to shadecast (wants eth0 by the rule `from 203.0.113.10`: 198.18.105.1 from 203.0.113.10 via 203.0.113.9 dev eth0 table 101 uid 0 cache)
- PASS: the packets of hc-b leave through eth1, the uplink to tumbleweed (wants eth1 by the rule `from 198.18.101.10`: 198.18.105.1 from 198.18.101.10 via 198.18.101.9 dev eth1 table 102 uid 0 cache)
- PASS: both VTIs are up, each with its IPsec SA installed (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas 2)
- PASS: the site reaches the head office's block through its primary tunnel (10.20.30.11 via 10.255.0.0 dev vti101 src 10.255.0.1 uid 0 cache 10.20.0.0/16 nhid 26 via 10.255.0.0 dev vti101 proto bgp metric 20)
- PASS: the head office reaches the site's block through the same tunnel (10.21.0.0/16 nhid 33 via 10.255.0.1 dev vti101 proto bgp metric 20)
- PASS: a host at the site reaches a host at the head office (--- 10.20.30.11 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 1807ms
rtt min/avg/max/mdev = 2.149/2.575/3.857/0.504 ms)
- PASS: the site moves to its second tunnel when its ShadeCast uplink goes silent (outage 2.8 s (14 of 300 pings lost; BFD detects in 3 s); now on vti102; --- 10.20.30.11 ping statistics --- 300 packets transmitted, 286 received, 4.66667% packet loss, time 60085ms rtt min/avg/max/md)
- PASS: the site returns to its primary tunnel after the repair (the fault dropped 384 packets; outage on the way back 0.0 s (0 lost); now on vti101)
- PASS: both tunnels are as configured after the site's uplink failed and came back (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas 2)
- PASS: the site keeps the head office when the head office loses its ShadeCast uplink (outage 2.2 s (11 of 300 lost); the site is on vti101; ShadeCast's route: 64501 64505 198.18.0.18 from 198.18.0.18 (198.18.101.254))
- PASS: both tunnels are as configured while the head office is on one ISP (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas 2)
- PASS: the repair costs nothing, and the site is on its primary tunnel (the fault dropped 406 packets; outage 0.0 s (0 lost); now on vti101)
- PASS: with its uplink's link down, a tunnel does not move to the other uplink (0 of the first tunnel's packets left through eth1 in 20 s; its route lookup now answers `RTNETLINK answers: Invalid argument` (a blackhole route answers Invalid argument); traffic on vti102)
- PASS: the tunnel returns when the link does (vti101)
- PASS: after 200 s of silence on its uplink, the first tunnel comes back by itself (on vti101 57 s after the repair (the fault dropped 650 packets). The first tunnel before the repair: hc-a: #3, CONNECTING, IKEv2, a7b2361d9713c1f0_i* 0000000000000000_r hc-b: #2, ESTABLISHED, IKEv2, b)
- PASS: both tunnels are as configured after all the faults (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas 2)

Versions:
- phenix: Branch afbcba7 [ghostofgoes/sceptre-phenix] (commit afbcba7) Sun Nov 22 23:48:22 UTC 2026
- minimega: ip-10-0-128-227: minimega 3.3.0 66ed1aacc8107f00cb003a512e835d6f32886dda 2026-11-22
- qemu: Copyright (c) 2003-2023 Fabrice Bellard and the QEMU Project developers
- ovs: DB Schema 8.5.1
- phenix-ref: afbcba70f488e222f0236114d162bbb526051784
- images-ref: f09368d753ef34177d192b51131b0d93b7b8e54b
