#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
# ------------------------------ Interfaces -------------------------------
set interface ethernet eth0 address 203.0.113.10/30
set interface ethernet eth1 address 198.18.101.10/30
set interface ethernet eth2 address 10.21.30.1/24
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
set system host-name hc-fw-edge
# ------------------------------ Extra config -----------------------------
set protocols bfd profile WAN interval transmit 1000
set protocols bfd profile WAN interval receive 1000
set protocols bfd profile WAN interval multiplier 3
set policy prefix-list SITE-NETS rule 10 action permit
set policy prefix-list SITE-NETS rule 10 prefix 10.0.0.0/8
set policy prefix-list SITE-NETS rule 10 ge 16
set policy prefix-list SITE-NETS rule 10 le 24
set policy route-map OVERLAY-PRIMARY rule 10 action permit
set policy route-map OVERLAY-PRIMARY rule 10 match ip address prefix-list SITE-NETS
set policy route-map OVERLAY-PRIMARY rule 10 set local-preference 200
set policy route-map OVERLAY-BACKUP rule 10 action permit
set policy route-map OVERLAY-BACKUP rule 10 match ip address prefix-list SITE-NETS
set policy route-map OVERLAY-BACKUP rule 10 set local-preference 100
set policy route-map OVERLAY-OUT rule 10 action permit
set policy route-map OVERLAY-OUT rule 10 match ip address prefix-list SITE-NETS
set protocols bgp system-as 65020
set protocols bgp parameters router-id 10.255.2.1
set protocols bgp parameters log-neighbor-changes
set protocols bgp address-family ipv4-unicast network 10.11.0.0/16
set protocols bgp address-family ipv4-unicast network 10.21.0.0/16
set protocols bgp address-family ipv4-unicast network 10.31.0.0/16
set protocols bgp address-family ipv4-unicast network 10.41.0.0/16
set protocols static route 0.0.0.0/0 next-hop 203.0.113.9 distance 10
set protocols static route 0.0.0.0/0 next-hop 198.18.101.9 distance 20
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
set interfaces vti vti101 description 'hc-a over ShadeCast Cable'
set interfaces vti vti101 address 10.255.0.1/31
set vpn ipsec authentication psk hc-a id '203.0.113.10'
set vpn ipsec authentication psk hc-a id '198.18.105.1'
set vpn ipsec authentication psk hc-a secret 'REDACTED'
set vpn ipsec site-to-site peer hc-a authentication mode pre-shared-secret
set vpn ipsec site-to-site peer hc-a authentication local-id '203.0.113.10'
set vpn ipsec site-to-site peer hc-a authentication remote-id '198.18.105.1'
set vpn ipsec site-to-site peer hc-a connection-type initiate
set vpn ipsec site-to-site peer hc-a ike-group WAN
set vpn ipsec site-to-site peer hc-a default-esp-group WAN
set vpn ipsec site-to-site peer hc-a local-address 203.0.113.10
set vpn ipsec site-to-site peer hc-a remote-address 198.18.105.1
set vpn ipsec site-to-site peer hc-a vti bind vti101
set vpn ipsec site-to-site peer hc-a vti esp-group WAN
set policy local-route rule 101 source address 203.0.113.10
set policy local-route rule 101 set table 101
set protocols static table 101 route 0.0.0.0/0 next-hop 203.0.113.9
set protocols static table 101 route 0.0.0.0/0 blackhole distance 250
set protocols bgp neighbor 10.255.0.0 remote-as 64505
set protocols bgp neighbor 10.255.0.0 description 'overlay hc-a'
set protocols bgp neighbor 10.255.0.0 timers keepalive 3
set protocols bgp neighbor 10.255.0.0 timers holdtime 9
set protocols bgp neighbor 10.255.0.0 timers connect 5
set protocols bgp neighbor 10.255.0.0 bfd profile WAN
set protocols bgp neighbor 10.255.0.0 address-family ipv4-unicast route-map import OVERLAY-PRIMARY
set protocols bgp neighbor 10.255.0.0 address-family ipv4-unicast route-map export OVERLAY-OUT
set interfaces vti vti102 description 'hc-b over Tumbleweed Fiber'
set interfaces vti vti102 address 10.255.0.3/31
set vpn ipsec authentication psk hc-b id '198.18.101.10'
set vpn ipsec authentication psk hc-b id '198.18.105.1'
set vpn ipsec authentication psk hc-b secret 'REDACTED'
set vpn ipsec site-to-site peer hc-b authentication mode pre-shared-secret
set vpn ipsec site-to-site peer hc-b authentication local-id '198.18.101.10'
set vpn ipsec site-to-site peer hc-b authentication remote-id '198.18.105.1'
set vpn ipsec site-to-site peer hc-b connection-type initiate
set vpn ipsec site-to-site peer hc-b ike-group WAN
set vpn ipsec site-to-site peer hc-b default-esp-group WAN
set vpn ipsec site-to-site peer hc-b local-address 198.18.101.10
set vpn ipsec site-to-site peer hc-b remote-address 198.18.105.1
set vpn ipsec site-to-site peer hc-b vti bind vti102
set vpn ipsec site-to-site peer hc-b vti esp-group WAN
set policy local-route rule 102 source address 198.18.101.10
set policy local-route rule 102 set table 102
set protocols static table 102 route 0.0.0.0/0 next-hop 198.18.101.9
set protocols static table 102 route 0.0.0.0/0 blackhole distance 250
set protocols bgp neighbor 10.255.0.2 remote-as 64505
set protocols bgp neighbor 10.255.0.2 description 'overlay hc-b'
set protocols bgp neighbor 10.255.0.2 timers keepalive 3
set protocols bgp neighbor 10.255.0.2 timers holdtime 9
set protocols bgp neighbor 10.255.0.2 timers connect 5
set protocols bgp neighbor 10.255.0.2 bfd profile WAN
set protocols bgp neighbor 10.255.0.2 address-family ipv4-unicast route-map import OVERLAY-BACKUP
set protocols bgp neighbor 10.255.0.2 address-family ipv4-unicast route-map export OVERLAY-OUT
commit
save
exit
