### Spike wan-bgp-vyos-vti-both: PASS

- PASS: phenix rendered the config list of shadecast after its own configuration, before the commit (69 commands; /phenix/experiments/spike-wan-bgp/vrouter/shadecast.boot)
- PASS: phenix rendered the config list of tumbleweed after its own configuration, before the commit (69 commands; /phenix/experiments/spike-wan-bgp/vrouter/tumbleweed.boot)
- PASS: phenix rendered the config list of fr-fw-edge after its own configuration, before the commit (240 commands; /phenix/experiments/spike-wan-bgp/vrouter/fr-fw-edge.boot)
- PASS: phenix rendered the config list of fc-fw-edge after its own configuration, before the commit (188 commands; /phenix/experiments/spike-wan-bgp/vrouter/fc-fw-edge.boot)
- PASS: shadecast committed the rendered configuration at first boot (20
Nov 24 08:29:13 vyos vyos-router[2671]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/vyos.script
Nov 24 08:29:37 shadecast commit[4739]: Successful change to active configurat)
- PASS: shadecast holds every line of its config list after the commit (all 69 lines are in `show configuration commands`)
- PASS: tumbleweed committed the rendered configuration at first boot (20
Nov 24 08:29:12 vyos commit[2641]: Successful change to active configuration by user root on unknown
Nov 24 08:29:13 vyos vyos-router[2681]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: tumbleweed holds every line of its config list after the commit (all 69 lines are in `show configuration commands`)
- PASS: fr-fw-edge committed the rendered configuration at first boot (44
Nov 24 08:29:18 vyos commit[2633]: Successful change to active configuration by user root on unknown
Nov 24 08:29:18 vyos vyos-router[2673]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fr-fw-edge holds every line of its config list after the commit (all 240 lines are in `show configuration commands`)
- PASS: fc-fw-edge committed the rendered configuration at first boot (21
Nov 24 08:29:22 vyos commit[2638]: Successful change to active configuration by user root on unknown
Nov 24 08:29:23 vyos vyos-router[2678]: /usr/lib/live/mount/persistence/boot/vyos/rw/config/scri)
- PASS: fc-fw-edge holds every line of its config list after the commit (all 188 lines are in `show configuration commands`)
- PASS: all 2 BGP sessions of shadecast are Established ({"198.18.0.18 (Tumbleweed Fiber)": "Established", "203.0.113.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 2 BGP sessions of tumbleweed are Established ({"198.18.0.17 (ShadeCast Cable)": "Established", "198.18.101.2 (Head office and Front Range brewery edge)": "Established"})
- PASS: all 4 BGP sessions of fr-fw-edge are Established ({"10.255.0.1 (overlay fc-a)": "Established", "10.255.0.3 (overlay fc-b)": "Established", "198.18.101.1 (Tumbleweed Fiber)": "Established", "203.0.113.1 (ShadeCast Cable)": "Established"})
- PASS: all 2 BGP sessions of fc-fw-edge are Established ({"10.255.0.0 (overlay fc-a)": "Established", "10.255.0.2 (overlay fc-b)": "Established"})
- PASS: the head office installs a default route through each ISP, with its endpoint as the source (default nhid 24 proto bgp src 198.18.105.1 metric 20 nexthop via 198.18.101.1 dev eth1 weight 1 nexthop via 203.0.113.1 dev eth0 weight 1)
- PASS: the packets of fc-a leave through eth0, the uplink to shadecast (wants eth0 by the rule `from 203.0.113.10`: 198.18.105.1 from 203.0.113.10 via 203.0.113.9 dev eth0 table 101 uid 0 cache)
- PASS: the packets of fc-b leave through eth1, the uplink to tumbleweed (wants eth1 by the rule `from 198.18.101.10`: 198.18.105.1 from 198.18.101.10 via 198.18.101.9 dev eth1 table 102 uid 0 cache)
- PASS: both VTIs are up, each with its IPsec SA installed (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 2 sas fc-b 2)
- PASS: the site reaches the head office's block through its primary tunnel (10.20.30.11 via 10.255.0.0 dev vti101 src 10.21.10.1 uid 0 cache 10.20.0.0/16 nhid 34 via 10.255.0.0 dev vti101 proto bgp src 10.21.10.1 metric 20)
- PASS: the head office reaches the site's block through the same tunnel (10.21.0.0/16 nhid 38 via 10.255.0.1 dev vti101 proto bgp src 10.20.30.1 metric 20)
- PASS: each edge sends its own traffic into a tunnel from its LAN address (fc-fw-edge: 10.20.30.11 via 10.255.0.0 dev vti101 src 10.21.10.1 uid 0 cache; fr-fw-edge: 10.21.10.11 via 10.255.0.1 dev vti101 src 10.20.30.1 uid 0 cache)
- PASS: a 1400-byte packet with DF set crosses each tunnel, and a larger one is refused at the edge ({"fc-a": ["3 packets transmitted, 3 received, 0% packet loss, time 2003ms rtt min/avg/max/mdev = 1.123/1.265/1.354/0.101 ms", "PING 10.255.0.0 (10.255.0.0) 1373(1401) bytes of data. ping: local error:)
- PASS: a host at the site reaches a host at the head office (--- 10.20.30.11 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 1807ms
rtt min/avg/max/mdev = 2.123/2.440/3.846/0.498 ms)
- PASS: no edge would send an unused private address out of an uplink (16 probes; 10.0.0.0/8 is a blackhole at 2 of 2 edges)
- PASS: the head office refuses another site's network when the site announces it (fc-fw-edge offered 192.168.20.0/24 on 2 tunnels: True (0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i *> 192.168.20.0/24 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list)
- PASS: the head office refuses a more-specific of the head office's own block when the site announces it (fc-fw-edge offered 10.20.99.0/24 on 2 tunnels: True (0 0 0 i *> 10.20.99.0/24 0.0.0.0 0 0 i *> 10.21.0.0/16 0.0.0.0 0 0 i Total number of prefixes 3, total number of paths 3). The head office's list o)
- PASS: the head office drops and logs a source that is not the site's when it comes out of the site's tunnel (forged source 10.12.0.99 (a block of ep-fw-edge); logged at the head office: 6 ipv4-FWD-filter-100-D]IN=vti101 6 ipv4-INP-filter-100-D]IN=vti101; from the site's own address: 3 packets transmitted, 3 )
- PASS: a host at the site reaches a public address as the address of the site's first uplink (5 packets transmitted, 5 received, 0% packet loss, time 802ms rtt min/avg/max/mdev = 1.677/1.869/2.093/0.161 ms; replies go to 203.0.113.10 (the uplink is 203.0.113.10))
- PASS: the site holds a failover default route for each uplink (default via 203.0.113.9 dev eth0 proto failover metric 10  | default via 198.18.101.9 dev eth1 proto failover metric 20 )
- PASS: a host at the head office reaches public addresses through both ISPs as the head office's NAT address (reached 6 of 6 ISP addresses; replies go to ['198.18.105.2']; uplinks used: ['eth0', 'eth1'])
- PASS: the head office sends each tunnel's packets through the ISP of the site's uplink (each tunnel returns through its own ISP: {"fc-a": "203.0.113.10 from 198.18.105.1 via 203.0.113.1 dev eth0 uid 0", "fc-b": "198.18.101.10 from 198.18.105.1 via 198.18.101.1 dev eth1 uid 0"})
- PASS: every BFD session is up at the configured interval before the faults (fr-fw-edge: 4 of 4 up; transmit intervals [1000] ms; fc-fw-edge: 2 of 2 up; transmit intervals [1000] ms)
- PASS: the site moves to its second tunnel when its ShadeCast uplink goes silent (outage 2.2 s (11 of 300 pings lost; BFD detects in 3 s); now on vti102; --- 10.20.30.11 ping statistics --- 300 packets transmitted, 289 received, 3.66667% packet loss, time 60118ms rtt min/avg/max/md)
- PASS: the site's default route moves to its second uplink, and a new flow reaches a public address (default routes: default via 198.18.101.9 dev eth1 proto failover metric 20  | ; 5 packets transmitted, 5 received, 0% packet loss, time 803ms rtt min/avg/max/mdev = 1.616/1.868/2.079/0.171 ms)
- PASS: the site's default route returns to its first uplink after the repair (default via 203.0.113.9 dev eth0 proto failover metric 10  | default via 198.18.101.9 dev eth1 proto failover metric 20  | )
- PASS: the site returns to its primary tunnel after the repair (the fault dropped 436 packets; outage on the way back 0.0 s (0 lost); now on vti101)
- PASS: both tunnels are as configured after the site's uplink failed and came back (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 2 sas fc-b 2)
- PASS: the site keeps the head office when the head office loses its ShadeCast uplink (outage 2.4 s (12 of 300 lost); the site stayed on vti101; ShadeCast's best path to the head office is '64501 64505')
- PASS: both tunnels are as configured while the head office is on one ISP (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 2 sas fc-b 2)
- PASS: the repair costs nothing, and the site is on its primary tunnel (the fault dropped 472 packets; outage 0.0 s (0 lost); now on vti101)
- PASS: the head office's ShadeCast fault never ends the BGP session of the site's second tunnel (the session of fc-b stayed up in all 6 rounds)
- PASS: in 6 rounds of the head office's uplink fault, every outage is shorter than the hold time (outages [2.4, 2.2, 2.2, 2.2, 2.2, 2.6] s; 0 of 6 rounds reached 4.0 s: [])
- PASS: with its uplink's link down, a tunnel does not move to the other uplink (0 of the first tunnel's packets left through eth1 in 20 s; its route lookup now answers `RTNETLINK answers: Invalid argument` (a blackhole route answers Invalid argument); traffic on vti102)
- PASS: the tunnel returns when the link does (vti101)
- PASS: after 200 s of silence on its uplink, the first tunnel comes back by itself (on vti101 23 s after the repair (the fault dropped 726 packets). The first tunnel before the repair: fc-a: #6, CONNECTING, IKEv2, 693e3bfc97bb64bd_i* 0000000000000000_r fc-a: #5, CONNECTING, IKEv2, 88)
- PASS: both tunnels are as configured after all the faults (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 3 sas fc-b 2)
- PASS: the head office's edge comes back from a reboot with its configuration (seconds after the reboot command: {"edge answers": 63, "its BGP sessions are up": 78}; 0 of 240 config lines missing: [])
- PASS: after the head office's edge restarts, the site reaches the head office again by itself (outage 59.2 s (299 of 3000 pings lost); seconds after the reboot command: {"edge answers": 63, "its BGP sessions are up": 78, "the site routes through its primary tunnel": 87, "traffic is back": 61}; )
- PASS: both tunnels are as configured after the head office's restart (vti101@NONE UNKNOWN <NOARP,UP,LOWER_UP> vti102@NONE UNKNOWN <NOARP,UP,LOWER_UP> sas fc-a 5 sas fc-b 3)
- PASS: each ISP takes its default route back when it loses the prefix that the route depends on (both default routes before: True; none 15 s after the ISPs lost each other (the BGP hold time is 9 s): (no default route))
- PASS: both default routes and the site's primary tunnel return when the ISPs peer again (both default routes 9 s after the repair: default nhid 26 proto bgp src 198.18.105.1 metric 20 nexthop via 198.18.101.1 dev eth1 weight 1 nexthop via 203.0.113.1 dev eth0 weight 1; the site is on vti1)

Versions:
- phenix: Branch afbcba7 [ghostofgoes/sceptre-phenix] (commit afbcba7) Tue Nov 24 08:24:50 UTC 2026
- minimega: ip-10-0-132-194: minimega 3.3.0 66ed1aacc8107f00cb003a512e835d6f32886dda 2026-11-24
- qemu: Copyright (c) 2003-2023 Fabrice Bellard and the QEMU Project developers
- ovs: DB Schema 8.5.1
- phenix-ref: afbcba70f488e222f0236114d162bbb526051784
- images-ref: f09368d753ef34177d192b51131b0d93b7b8e54b
