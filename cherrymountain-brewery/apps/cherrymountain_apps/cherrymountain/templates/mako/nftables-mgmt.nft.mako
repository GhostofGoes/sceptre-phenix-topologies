# MGMT nftables policy for ${hostname}, rendered by the cherrymountain app
# (DESIGN.md 10.1). It matches only the MGMT interface; the plant NICs are
# untouched. Loaded by /etc/phenix/startup/4-cm-mgmt-nftables.sh, which passes
# the device name as the variable mgmt_if (nft -D mgmt_if=<device>): the
# topology calls the interface ${iface}, but phenix does not rename the guest's
# NIC, so the script looks the device up by this host's MGMT address
# (${address}).
#
# Allowed on MGMT:
#   - ICMP echo to sim-ctl (the helics app's broker-wait script pings it)
#   - HELICS zmq to the broker (tcp ${broker_ports}) and from the broker to
#     the federates' cores (tcp ${federate_ports}; the broker assigns each
#     core a port counted up from just above its own, per host)
#   - OpenSearch (tcp ${opensearch_port}) and NTP (udp 123) to sim-ctl
# Everything else in or out of the MGMT interface is counted and dropped.

table inet cm_mgmt
delete table inet cm_mgmt

table inet cm_mgmt {
	set peers {
		type ipv4_addr
		elements = { ${", ".join(peers)} }
	}

	chain input {
		type filter hook input priority filter; policy accept;
		iifname != $mgmt_if accept
		ct state established,related accept
		ct state invalid drop
		meta l4proto ipv6-icmp accept
% if hub:
		ip saddr @peers icmp type echo-request accept
		ip saddr @peers tcp dport { ${broker_ports} } accept
		ip saddr @peers tcp dport ${opensearch_port} accept
		ip saddr @peers udp dport 123 accept
% endif
		ip saddr @peers tcp dport ${federate_ports} accept
		counter drop
	}

	chain output {
		type filter hook output priority filter; policy accept;
		oifname != $mgmt_if accept
		ct state established,related accept
		meta l4proto ipv6-icmp accept
% if not hub:
		ip daddr ${hub_ip} icmp type echo-request accept
		ip daddr ${hub_ip} tcp dport { ${broker_ports} } accept
		ip daddr ${hub_ip} tcp dport ${opensearch_port} accept
		ip daddr ${hub_ip} udp dport 123 accept
% endif
		ip daddr @peers tcp dport ${federate_ports} accept
		counter drop
	}
}
