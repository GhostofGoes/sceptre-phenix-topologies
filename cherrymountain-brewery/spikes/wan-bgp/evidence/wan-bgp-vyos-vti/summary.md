### Spike wan-bgp-vyos-vti: PASS

- PASS: phenix rendered the config list of shadecast after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/shadecast.boot)
- PASS: phenix rendered the config list of tumbleweed after its own configuration, before the commit (65 commands; /phenix/experiments/spike-wan-bgp/vrouter/tumbleweed.boot)
- PASS: phenix rendered the config list of fr-fw-edge after its own configuration, before the commit (201 commands; /phenix/experiments/spike-wan-bgp/vrouter/fr-fw-edge.boot)
- PASS: phenix rendered the config list of fc-fw-edge after its own configuration, before the commit (173 commands; /phenix/experiments/spike-wan-bgp/vrouter/fc-fw-edge.boot)
- PASS: shadecast committed the rendered configuration at first boot (20
Nov 23 07:38:05 vyos vyos-router[2687]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
Nov 23 07:38:35 shadecast commit[4669]: Successful change to active configurat)
- PASS: shadecast holds every line of its config list after the commit (all 65 lines are in `show configuration commands`)
- PASS: tumbleweed committed the rendered configuration at first boot (20
Nov 23 07:38:07 vyos commit[2630]: Successful change to active configuration by user root on unknown
Nov 23 07:38:07 vyos vyos-router[2669]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: tumbleweed holds every line of its config list after the commit (all 65 lines are in `show configuration commands`)
- PASS: fr-fw-edge committed the rendered configuration at first boot (44
Nov 23 07:38:02 vyos commit[2635]: Successful change to active configuration by user root on unknown
Nov 23 07:38:02 vyos vyos-router[2672]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fr-fw-edge holds every line of its config list after the commit (all 201 lines are in `show configuration commands`)
- PASS: fc-fw-edge committed the rendered configuration at first boot (21
Nov 23 07:38:05 vyos commit[2628]: Successful change to active configuration by user root on unknown
Nov 23 07:38:06 vyos vyos-router[2666]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fc-fw-edge holds every line of its config list after the commit (all 173 lines are in `show configuration commands`)
- PASS: all 2 BGP sessions of shadecast are Established ({"198.18.0.18 (Tumbleweed Fiber)": "Established", "203.0.113.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 2 BGP sessions of tumbleweed are Established ({"198.18.0.17 (ShadeCast Cable)": "Established", "198.18.101.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 4 BGP sessions of fr-fw-edge are Established ({"10.255.0.1 (overlay fc-a)": "Established", "10.255.0.3 (overlay fc-b)": "Established", "198.18.101.1 (Tumbleweed Fiber)": "Established", "203.0.113.1 (ShadeCast Cable)": "Established"})
- PASS: all 2 BGP sessions of fc-fw-edge are Established ({"10.255.0.0 (overlay fc-a)": "Established", "10.255.0.2 (overlay fc-b)": "Established"})
- PASS: the head office installs a default route through each ISP, with its endpoint as the source (default nhid 24 proto bgp src 198.18.105.1 metric 20 nexthop via 198.18.101.1 dev eth1 weight 1 nexthop via 203.0.113.1 dev eth0 weight 1)
- PASS: the packets of fc-a leave through eth0, the uplink to shadecast (wants eth0 by the rule `from 203.0.113.10`: 198.18.105.1 from 203.0.113.10 via 203.0.113.9 dev eth0 table 101 uid 0 cache)
- PASS: the packets of fc-b leave through eth1, the uplink to tumbleweed (wants eth1 by the rule `from 198.18.101.10`: 198.18.105.1 from 198.18.101.10 via 198.18.101.9 dev eth1 table 102 uid 0 cache)
- PASS: both VTIs are up, each with its IPsec SA installed (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 1 sas fc-b 1)
- PASS: the site reaches the head office's block through its primary tunnel (10.20.30.11 via 10.255.0.0 dev vti101 src 10.21.10.1 uid 0 cache 10.20.0.0/16 nhid 26 via 10.255.0.0 dev vti101 proto bgp src 10.21.10.1 metric 20)
- PASS: the head office reaches the site's block through the same tunnel (10.21.0.0/16 nhid 36 via 10.255.0.1 dev vti101 proto bgp src 10.20.30.1 metric 20)
- PASS: each edge sends its own traffic into a tunnel from its LAN address (fc-fw-edge: 10.20.30.11 via 10.255.0.0 dev vti101 src 10.21.10.1 uid 0 cache; fr-fw-edge: 10.21.10.11 via 10.255.0.1 dev vti101 src 10.20.30.1 uid 0 cache)
- PASS: a 1400-byte packet with DF set crosses each tunnel, and a larger one is refused at the edge ({"fc-a": ["3 packets transmitted, 3 received, 0% packet loss, time 2003ms rtt min/avg/max/mdev = 1.380/1.414/1.451/0.029 ms", "PING 10.255.0.0 (10.255.0.0) 1373(1401) bytes of data. ping: local error:)
- PASS: a host at the site reaches a host at the head office (--- 10.20.30.11 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 1806ms
rtt min/avg/max/mdev = 1.924/2.347/3.657/0.455 ms)
- PASS: no edge would send an unused private address out of an uplink (16 probes; 10.0.0.0/8 is a blackhole at 2 of 2 edges)
- PASS: the head office refuses another site's network when the site announces it (fc-fw-edge offered 192.168.20.0/24 on 2 tunnels: True (0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i *> 192.168.20.0/24 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list)
- PASS: the head office refuses a more-specific of the head office's own block when the site announces it (fc-fw-edge offered 10.20.99.0/24 on 2 tunnels: True (0 0 0 i *> 10.20.99.0/24 0.0.0.0 0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list o)
- PASS: every BFD session is up at the configured interval before the faults (fr-fw-edge: 4 of 4 up; transmit intervals [1000] ms; fc-fw-edge: 2 of 2 up; transmit intervals [1000] ms)
- PASS: the site moves to its second tunnel when its ShadeCast uplink goes silent (outage 3.0 s (15 of 300 pings lost; BFD detects in 3 s); now on vti102; --- 10.20.30.11 ping statistics --- 300 packets transmitted, 285 received, 5% packet loss, time 60109ms rtt min/avg/max/mdev = 1)
- PASS: the site returns to its primary tunnel after the repair (the fault dropped 384 packets; outage on the way back 0.0 s (0 lost); now on vti101)
- PASS: both tunnels are as configured after the site's uplink failed and came back (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 1 sas fc-b 1)
- PASS: the site keeps the head office when the head office loses its ShadeCast uplink (outage 2.2 s (11 of 300 lost); the site stayed on vti101; ShadeCast's best path to the head office is '64501 64505')
- PASS: both tunnels are as configured while the head office is on one ISP (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 1 sas fc-b 1)
- PASS: the repair costs nothing, and the site is on its primary tunnel (the fault dropped 423 packets; outage 0.0 s (0 lost); now on vti101)
- PASS: with its uplink's link down, a tunnel does not move to the other uplink (0 of the first tunnel's packets left through eth1 in 20 s; its route lookup now answers `RTNETLINK answers: Invalid argument` (a blackhole route answers Invalid argument); traffic on vti102)
- PASS: the tunnel returns when the link does (vti101)
- PASS: after 200 s of silence on its uplink, the first tunnel comes back by itself (on vti101 31 s after the repair (the fault dropped 757 packets). The first tunnel before the repair: fc-a: #3, CONNECTING, IKEv2, 858fe9ab00cc4ed7_i* 0000000000000000_r fc-b: #2, ESTABLISHED, IKEv2, c)
- PASS: both tunnels are as configured after all the faults (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 1 sas fc-b 1)

Versions:
- phenix: Branch afbcba7 [ghostofgoes/sceptre-phenix] (commit afbcba7) Mon Nov 23 07:33:43 UTC 2026
- minimega: ip-10-0-137-158: minimega 3.3.0 66ed1aacc8107f00cb003a512e835d6f32886dda 2026-11-23
- qemu: Copyright (c) 2003-2023 Fabrice Bellard and the QEMU Project developers
- ovs: DB Schema 8.5.1
- phenix-ref: afbcba70f488e222f0236114d162bbb526051784
- images-ref: f09368d753ef34177d192b51131b0d93b7b8e54b
