#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
# ------------------------------ Interfaces -------------------------------
set interface ethernet eth0 address 203.0.113.10/30
set interface ethernet eth1 address 198.18.101.10/30
set interface ethernet eth2 address 10.21.10.1/24
# ---------------------------------- SNAT ---------------------------------
# ---------------------------------- DNAT ---------------------------------
# ----------------------------- Traffic-Policy ----------------------------
# -------------------------------- Firewall -------------------------------
# --------------------------------- Routes --------------------------------
# ---------------------------------- OSPF ---------------------------------
# --------------------------------- IPsec ---------------------------------
# -------------------------------- Services -------------------------------
# SSH
# NTP
delete service ntp
# SNMP
# --------------------------------- System --------------------------------
set system host-name fc-fw-edge
# ------------------------------ Extra config -----------------------------
set protocols bfd profile WAN interval transmit 1000
set protocols bfd profile WAN interval receive 1000
set protocols bfd profile WAN interval multiplier 3
set policy prefix-list SITE-FR rule 10 action permit
set policy prefix-list SITE-FR rule 10 prefix 10.10.0.0/16
set policy prefix-list SITE-FR rule 10 le 24
set policy prefix-list SITE-NETS rule 10 action permit
set policy prefix-list SITE-NETS rule 10 prefix 10.10.0.0/16
set policy prefix-list SITE-NETS rule 10 le 24
set policy prefix-list SITE-FR rule 20 action permit
set policy prefix-list SITE-FR rule 20 prefix 10.20.0.0/16
set policy prefix-list SITE-FR rule 20 le 24
set policy prefix-list SITE-NETS rule 20 action permit
set policy prefix-list SITE-NETS rule 20 prefix 10.20.0.0/16
set policy prefix-list SITE-NETS rule 20 le 24
set policy prefix-list SITE-FR rule 30 action permit
set policy prefix-list SITE-FR rule 30 prefix 10.30.0.0/16
set policy prefix-list SITE-FR rule 30 le 24
set policy prefix-list SITE-NETS rule 30 action permit
set policy prefix-list SITE-NETS rule 30 prefix 10.30.0.0/16
set policy prefix-list SITE-NETS rule 30 le 24
set policy prefix-list SITE-FR rule 40 action permit
set policy prefix-list SITE-FR rule 40 prefix 10.40.0.0/16
set policy prefix-list SITE-FR rule 40 le 24
set policy prefix-list SITE-NETS rule 40 action permit
set policy prefix-list SITE-NETS rule 40 prefix 10.40.0.0/16
set policy prefix-list SITE-NETS rule 40 le 24
set policy prefix-list SITE-FC rule 10 action permit
set policy prefix-list SITE-FC rule 10 prefix 10.11.0.0/16
set policy prefix-list SITE-FC rule 10 le 24
set policy prefix-list SITE-NETS rule 50 action permit
set policy prefix-list SITE-NETS rule 50 prefix 10.11.0.0/16
set policy prefix-list SITE-NETS rule 50 le 24
set policy prefix-list SITE-FC rule 20 action permit
set policy prefix-list SITE-FC rule 20 prefix 10.21.0.0/16
set policy prefix-list SITE-FC rule 20 le 24
set policy prefix-list SITE-NETS rule 60 action permit
set policy prefix-list SITE-NETS rule 60 prefix 10.21.0.0/16
set policy prefix-list SITE-NETS rule 60 le 24
set policy prefix-list SITE-EP rule 10 action permit
set policy prefix-list SITE-EP rule 10 prefix 10.12.0.0/16
set policy prefix-list SITE-EP rule 10 le 24
set policy prefix-list SITE-NETS rule 70 action permit
set policy prefix-list SITE-NETS rule 70 prefix 10.12.0.0/16
set policy prefix-list SITE-NETS rule 70 le 24
set policy prefix-list SITE-EP rule 20 action permit
set policy prefix-list SITE-EP rule 20 prefix 10.22.0.0/16
set policy prefix-list SITE-EP rule 20 le 24
set policy prefix-list SITE-NETS rule 80 action permit
set policy prefix-list SITE-NETS rule 80 prefix 10.22.0.0/16
set policy prefix-list SITE-NETS rule 80 le 24
set policy prefix-list SITE-EP rule 30 action permit
set policy prefix-list SITE-EP rule 30 prefix 10.32.0.0/16
set policy prefix-list SITE-EP rule 30 le 24
set policy prefix-list SITE-NETS rule 90 action permit
set policy prefix-list SITE-NETS rule 90 prefix 10.32.0.0/16
set policy prefix-list SITE-NETS rule 90 le 24
set policy prefix-list SITE-EP rule 40 action permit
set policy prefix-list SITE-EP rule 40 prefix 10.42.0.0/16
set policy prefix-list SITE-EP rule 40 le 24
set policy prefix-list SITE-NETS rule 100 action permit
set policy prefix-list SITE-NETS rule 100 prefix 10.42.0.0/16
set policy prefix-list SITE-NETS rule 100 le 24
set policy prefix-list SITE-CS rule 10 action permit
set policy prefix-list SITE-CS rule 10 prefix 192.168.10.0/24
set policy prefix-list SITE-NETS rule 110 action permit
set policy prefix-list SITE-NETS rule 110 prefix 192.168.10.0/24
set policy prefix-list SITE-CS rule 20 action permit
set policy prefix-list SITE-CS rule 20 prefix 192.168.20.0/24
set policy prefix-list SITE-NETS rule 120 action permit
set policy prefix-list SITE-NETS rule 120 prefix 192.168.20.0/24
set policy prefix-list SITE-CS rule 30 action permit
set policy prefix-list SITE-CS rule 30 prefix 192.168.30.0/24
set policy prefix-list SITE-NETS rule 130 action permit
set policy prefix-list SITE-NETS rule 130 prefix 192.168.30.0/24
set policy route-map OVERLAY-PRIMARY rule 10 action permit
set policy route-map OVERLAY-PRIMARY rule 10 match ip address prefix-list SITE-NETS
set policy route-map OVERLAY-PRIMARY rule 10 set local-preference 200
set policy route-map OVERLAY-BACKUP rule 10 action permit
set policy route-map OVERLAY-BACKUP rule 10 match ip address prefix-list SITE-NETS
set policy route-map OVERLAY-BACKUP rule 10 set local-preference 100
set policy route-map OVERLAY-OUT rule 10 action permit
set policy route-map OVERLAY-OUT rule 10 match ip address prefix-list SITE-FC
set protocols bgp system-as 65020
set protocols bgp parameters router-id 10.255.2.1
set protocols bgp parameters log-neighbor-changes
set protocols bgp address-family ipv4-unicast network 10.11.0.0/16
set protocols bgp address-family ipv4-unicast network 10.21.0.0/16
set protocols failover route 0.0.0.0/0 next-hop 203.0.113.9 interface eth0
set protocols failover route 0.0.0.0/0 next-hop 203.0.113.9 metric 10
set protocols failover route 0.0.0.0/0 next-hop 203.0.113.9 check target 203.0.113.9 interface eth0
set protocols failover route 0.0.0.0/0 next-hop 203.0.113.9 check timeout 2
set protocols failover route 0.0.0.0/0 next-hop 198.18.101.9 interface eth1
set protocols failover route 0.0.0.0/0 next-hop 198.18.101.9 metric 20
set protocols failover route 0.0.0.0/0 next-hop 198.18.101.9 check target 198.18.101.9 interface eth1
set protocols failover route 0.0.0.0/0 next-hop 198.18.101.9 check timeout 2
set policy route-map SET-SOURCE rule 15 action permit
set policy route-map SET-SOURCE rule 15 match ip address prefix-list SITE-NETS
set policy route-map SET-SOURCE rule 15 set src 10.21.10.1
set policy route-map SET-SOURCE rule 20 action permit
set system ip protocol bgp route-map SET-SOURCE
set protocols static route 10.11.0.0/16 blackhole distance 250
set protocols static route 10.21.0.0/16 blackhole distance 250
set protocols static route 10.0.0.0/8 blackhole distance 254
set protocols static route 172.16.0.0/12 blackhole distance 254
set protocols static route 192.168.0.0/16 blackhole distance 254
set interfaces wireguard wg101 description 'fc-a over ShadeCast Cable'
set interfaces wireguard wg101 address 10.255.0.1/31
set interfaces wireguard wg101 private-key 'REDACTED'
set interfaces wireguard wg101 peer fc-a public-key 'REDACTED'
set interfaces wireguard wg101 peer fc-a allowed-ips 0.0.0.0/0
set interfaces wireguard wg101 peer fc-a address 198.18.105.1
set interfaces wireguard wg101 peer fc-a port 51821
set interfaces wireguard wg101 peer fc-a persistent-keepalive 5
set interfaces wireguard wg101 fwmark 101
set policy local-route rule 101 fwmark 101
set policy local-route rule 101 set table 101
set protocols static table 101 route 0.0.0.0/0 next-hop 203.0.113.9
set protocols static table 101 route 0.0.0.0/0 blackhole distance 250
set policy local-route rule 151 fwmark 101
set policy local-route rule 151 set table 151
set protocols static table 151 route 0.0.0.0/0 blackhole
set protocols bgp neighbor 10.255.0.0 remote-as 64505
set protocols bgp neighbor 10.255.0.0 description 'overlay fc-a'
set protocols bgp neighbor 10.255.0.0 timers keepalive 3
set protocols bgp neighbor 10.255.0.0 timers holdtime 9
set protocols bgp neighbor 10.255.0.0 timers connect 5
set protocols bgp neighbor 10.255.0.0 bfd profile WAN
set protocols bgp neighbor 10.255.0.0 address-family ipv4-unicast route-map import OVERLAY-PRIMARY
set protocols bgp neighbor 10.255.0.0 address-family ipv4-unicast route-map export OVERLAY-OUT
set interfaces wireguard wg102 description 'fc-b over Tumbleweed Fiber'
set interfaces wireguard wg102 address 10.255.0.3/31
set interfaces wireguard wg102 private-key 'REDACTED'
set interfaces wireguard wg102 peer fc-b public-key 'REDACTED'
set interfaces wireguard wg102 peer fc-b allowed-ips 0.0.0.0/0
set interfaces wireguard wg102 peer fc-b address 198.18.105.1
set interfaces wireguard wg102 peer fc-b port 51822
set interfaces wireguard wg102 peer fc-b persistent-keepalive 5
set interfaces wireguard wg102 fwmark 102
set policy local-route rule 102 fwmark 102
set policy local-route rule 102 set table 102
set protocols static table 102 route 0.0.0.0/0 next-hop 198.18.101.9
set protocols static table 102 route 0.0.0.0/0 blackhole distance 250
set policy local-route rule 152 fwmark 102
set policy local-route rule 152 set table 152
set protocols static table 152 route 0.0.0.0/0 blackhole
set protocols bgp neighbor 10.255.0.2 remote-as 64505
set protocols bgp neighbor 10.255.0.2 description 'overlay fc-b'
set protocols bgp neighbor 10.255.0.2 timers keepalive 3
set protocols bgp neighbor 10.255.0.2 timers holdtime 9
set protocols bgp neighbor 10.255.0.2 timers connect 5
set protocols bgp neighbor 10.255.0.2 bfd profile WAN
set protocols bgp neighbor 10.255.0.2 address-family ipv4-unicast route-map import OVERLAY-BACKUP
set protocols bgp neighbor 10.255.0.2 address-family ipv4-unicast route-map export OVERLAY-OUT
set firewall group network-group PRIVATE network 10.0.0.0/8
set firewall group network-group PRIVATE network 172.16.0.0/12
set firewall group network-group PRIVATE network 192.168.0.0/16
set nat source rule 100 outbound-interface name eth0
set nat source rule 100 source group network-group PRIVATE
set nat source rule 100 translation address masquerade
set nat source rule 110 outbound-interface name eth1
set nat source rule 110 source group network-group PRIVATE
set nat source rule 110 translation address masquerade
commit
save
exit
