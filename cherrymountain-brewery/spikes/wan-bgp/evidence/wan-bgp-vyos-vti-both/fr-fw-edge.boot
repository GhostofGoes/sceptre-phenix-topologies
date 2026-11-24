#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
# ------------------------------ Interfaces -------------------------------
set interface ethernet eth0 address 203.0.113.2/30
set interface ethernet eth1 address 198.18.101.2/30
set interface ethernet eth2 address 10.20.30.1/24
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
set system host-name fr-fw-edge
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
set policy route-map OVERLAY-PRIMARY-FC rule 10 action permit
set policy route-map OVERLAY-PRIMARY-FC rule 10 match ip address prefix-list SITE-FC
set policy route-map OVERLAY-PRIMARY-FC rule 10 set local-preference 200
set policy route-map OVERLAY-BACKUP-FC rule 10 action permit
set policy route-map OVERLAY-BACKUP-FC rule 10 match ip address prefix-list SITE-FC
set policy route-map OVERLAY-BACKUP-FC rule 10 set local-preference 100
set policy route-map OVERLAY-OUT rule 10 action permit
set policy route-map OVERLAY-OUT rule 10 match ip address prefix-list SITE-NETS
set protocols bgp system-as 64505
set protocols bgp parameters router-id 198.18.105.1
set protocols bgp parameters log-neighbor-changes
set protocols bgp address-family ipv4-unicast network 10.10.0.0/16
set protocols bgp address-family ipv4-unicast network 10.20.0.0/16
set protocols bgp address-family ipv4-unicast network 10.30.0.0/16
set protocols bgp address-family ipv4-unicast network 10.40.0.0/16
set interfaces dummy dum0 address 198.18.105.1/32
set policy prefix-list INTERNET rule 1 action deny
set policy prefix-list INTERNET rule 1 prefix 10.0.0.0/8
set policy prefix-list INTERNET rule 1 le 32
set policy prefix-list INTERNET rule 2 action deny
set policy prefix-list INTERNET rule 2 prefix 172.16.0.0/12
set policy prefix-list INTERNET rule 2 le 32
set policy prefix-list INTERNET rule 3 action deny
set policy prefix-list INTERNET rule 3 prefix 192.168.0.0/16
set policy prefix-list INTERNET rule 3 le 32
set policy prefix-list INTERNET rule 10 action permit
set policy prefix-list INTERNET rule 10 prefix 0.0.0.0/0
set policy prefix-list INTERNET rule 10 le 24
set policy prefix-list OWN rule 10 action permit
set policy prefix-list OWN rule 10 prefix 198.18.105.0/24
set policy route-map FROM-ISP rule 10 action permit
set policy route-map FROM-ISP rule 10 match ip address prefix-list INTERNET
set policy route-map OWN-ONLY rule 10 action permit
set policy route-map OWN-ONLY rule 10 match ip address prefix-list OWN
set policy route-map SET-SOURCE rule 10 action permit
set policy route-map SET-SOURCE rule 10 match ip address prefix-list INTERNET
set policy route-map SET-SOURCE rule 10 set src 198.18.105.1
set protocols bgp parameters bestpath as-path multipath-relax
set protocols bgp address-family ipv4-unicast maximum-paths ebgp 2
set protocols bgp address-family ipv4-unicast network 198.18.105.0/24
set protocols static route 198.18.105.0/24 blackhole distance 250
set protocols bgp neighbor 203.0.113.1 remote-as 64500
set protocols bgp neighbor 203.0.113.1 description 'ShadeCast Cable'
set protocols bgp neighbor 203.0.113.1 timers keepalive 3
set protocols bgp neighbor 203.0.113.1 timers holdtime 9
set protocols bgp neighbor 203.0.113.1 timers connect 5
set protocols bgp neighbor 203.0.113.1 bfd profile WAN
set protocols bgp neighbor 203.0.113.1 address-family ipv4-unicast route-map import FROM-ISP
set protocols bgp neighbor 203.0.113.1 address-family ipv4-unicast route-map export OWN-ONLY
set protocols bgp neighbor 203.0.113.1 address-family ipv4-unicast soft-reconfiguration inbound
set protocols bgp neighbor 198.18.101.1 remote-as 64501
set protocols bgp neighbor 198.18.101.1 description 'Tumbleweed Fiber'
set protocols bgp neighbor 198.18.101.1 timers keepalive 3
set protocols bgp neighbor 198.18.101.1 timers holdtime 9
set protocols bgp neighbor 198.18.101.1 timers connect 5
set protocols bgp neighbor 198.18.101.1 bfd profile WAN
set protocols bgp neighbor 198.18.101.1 address-family ipv4-unicast route-map import FROM-ISP
set protocols bgp neighbor 198.18.101.1 address-family ipv4-unicast route-map export OWN-ONLY
set protocols bgp neighbor 198.18.101.1 address-family ipv4-unicast soft-reconfiguration inbound
set policy route-map SET-SOURCE rule 15 action permit
set policy route-map SET-SOURCE rule 15 match ip address prefix-list SITE-NETS
set policy route-map SET-SOURCE rule 15 set src 10.20.30.1
set policy route-map SET-SOURCE rule 20 action permit
set system ip protocol bgp route-map SET-SOURCE
set protocols static route 10.10.0.0/16 blackhole distance 250
set protocols static route 10.20.0.0/16 blackhole distance 250
set protocols static route 10.30.0.0/16 blackhole distance 250
set protocols static route 10.40.0.0/16 blackhole distance 250
set protocols static route 10.0.0.0/8 blackhole distance 254
set protocols static route 172.16.0.0/12 blackhole distance 254
set protocols static route 192.168.0.0/16 blackhole distance 254
set vpn ipsec options disable-route-autoinstall
set vpn ipsec ike-group WAN key-exchange ikev2
set vpn ipsec ike-group WAN lifetime 28800
set vpn ipsec ike-group WAN close-action start
set vpn ipsec ike-group WAN dead-peer-detection action restart
set vpn ipsec ike-group WAN dead-peer-detection interval 10
set vpn ipsec ike-group WAN dead-peer-detection timeout 30
set vpn ipsec ike-group WAN disable-mobike
set vpn ipsec ike-group WAN proposal 10 dh-group 14
set vpn ipsec ike-group WAN proposal 10 encryption aes256
set vpn ipsec ike-group WAN proposal 10 hash sha256
set vpn ipsec esp-group WAN lifetime 3600
set vpn ipsec esp-group WAN pfs dh-group14
set vpn ipsec esp-group WAN proposal 10 encryption aes256
set vpn ipsec esp-group WAN proposal 10 hash sha256
set interfaces vti vti101 description 'fc-a over ShadeCast Cable'
set interfaces vti vti101 address 10.255.0.0/31
set interfaces vti vti101 mtu 1400
set interfaces vti vti101 ip adjust-mss clamp-mss-to-pmtu
set vpn ipsec authentication psk fc-a id '198.18.105.1'
set vpn ipsec authentication psk fc-a id '203.0.113.10'
set vpn ipsec authentication psk fc-a secret 'REDACTED'
set vpn ipsec site-to-site peer fc-a authentication mode pre-shared-secret
set vpn ipsec site-to-site peer fc-a authentication local-id '198.18.105.1'
set vpn ipsec site-to-site peer fc-a authentication remote-id '203.0.113.10'
set vpn ipsec site-to-site peer fc-a connection-type initiate
set vpn ipsec site-to-site peer fc-a ike-group WAN
set vpn ipsec site-to-site peer fc-a default-esp-group WAN
set vpn ipsec site-to-site peer fc-a local-address 198.18.105.1
set vpn ipsec site-to-site peer fc-a remote-address 203.0.113.10
set vpn ipsec site-to-site peer fc-a vti bind vti101
set vpn ipsec site-to-site peer fc-a vti esp-group WAN
set protocols bgp neighbor 10.255.0.1 remote-as 65020
set protocols bgp neighbor 10.255.0.1 description 'overlay fc-a'
set protocols bgp neighbor 10.255.0.1 timers keepalive 3
set protocols bgp neighbor 10.255.0.1 timers holdtime 9
set protocols bgp neighbor 10.255.0.1 timers connect 5
set protocols bgp neighbor 10.255.0.1 bfd profile WAN
set protocols bgp neighbor 10.255.0.1 address-family ipv4-unicast route-map import OVERLAY-PRIMARY-FC
set protocols bgp neighbor 10.255.0.1 address-family ipv4-unicast route-map export OVERLAY-OUT
set interfaces vti vti102 description 'fc-b over Tumbleweed Fiber'
set interfaces vti vti102 address 10.255.0.2/31
set interfaces vti vti102 mtu 1400
set interfaces vti vti102 ip adjust-mss clamp-mss-to-pmtu
set vpn ipsec authentication psk fc-b id '198.18.105.1'
set vpn ipsec authentication psk fc-b id '198.18.101.10'
set vpn ipsec authentication psk fc-b secret 'REDACTED'
set vpn ipsec site-to-site peer fc-b authentication mode pre-shared-secret
set vpn ipsec site-to-site peer fc-b authentication local-id '198.18.105.1'
set vpn ipsec site-to-site peer fc-b authentication remote-id '198.18.101.10'
set vpn ipsec site-to-site peer fc-b connection-type initiate
set vpn ipsec site-to-site peer fc-b ike-group WAN
set vpn ipsec site-to-site peer fc-b default-esp-group WAN
set vpn ipsec site-to-site peer fc-b local-address 198.18.105.1
set vpn ipsec site-to-site peer fc-b remote-address 198.18.101.10
set vpn ipsec site-to-site peer fc-b vti bind vti102
set vpn ipsec site-to-site peer fc-b vti esp-group WAN
set protocols bgp neighbor 10.255.0.3 remote-as 65020
set protocols bgp neighbor 10.255.0.3 description 'overlay fc-b'
set protocols bgp neighbor 10.255.0.3 timers keepalive 3
set protocols bgp neighbor 10.255.0.3 timers holdtime 9
set protocols bgp neighbor 10.255.0.3 timers connect 5
set protocols bgp neighbor 10.255.0.3 bfd profile WAN
set protocols bgp neighbor 10.255.0.3 address-family ipv4-unicast route-map import OVERLAY-BACKUP-FC
set protocols bgp neighbor 10.255.0.3 address-family ipv4-unicast route-map export OVERLAY-OUT
set firewall group network-group PRIVATE network 10.0.0.0/8
set firewall group network-group PRIVATE network 172.16.0.0/12
set firewall group network-group PRIVATE network 192.168.0.0/16
set nat source rule 100 outbound-interface name eth0
set nat source rule 100 source group network-group PRIVATE
set nat source rule 100 translation address 198.18.105.2
set nat source rule 110 outbound-interface name eth1
set nat source rule 110 source group network-group PRIVATE
set nat source rule 110 translation address 198.18.105.2
set firewall group network-group VPN-FC network 10.11.0.0/16
set firewall group network-group VPN-FC network 10.21.0.0/16
set firewall group network-group VPN-FC network 10.255.0.1/32
set firewall group network-group VPN-FC network 10.255.0.3/32
set firewall ipv4 forward filter rule 100 action drop
set firewall ipv4 forward filter rule 100 inbound-interface name vti101
set firewall ipv4 forward filter rule 100 source group network-group '!VPN-FC'
set firewall ipv4 forward filter rule 100 log
set firewall ipv4 input filter rule 100 action drop
set firewall ipv4 input filter rule 100 inbound-interface name vti101
set firewall ipv4 input filter rule 100 source group network-group '!VPN-FC'
set firewall ipv4 input filter rule 100 log
set firewall ipv4 forward filter rule 101 action drop
set firewall ipv4 forward filter rule 101 inbound-interface name vti102
set firewall ipv4 forward filter rule 101 source group network-group '!VPN-FC'
set firewall ipv4 forward filter rule 101 log
set firewall ipv4 input filter rule 101 action drop
set firewall ipv4 input filter rule 101 inbound-interface name vti102
set firewall ipv4 input filter rule 101 source group network-group '!VPN-FC'
set firewall ipv4 input filter rule 101 log
commit
save
exit
