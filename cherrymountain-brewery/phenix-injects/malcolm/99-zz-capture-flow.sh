#!/bin/bash
# phenix startup hook on malcolm (DESIGN.md 14, capture requirement 1).
# The mirror app's setupOVS script (99-setup-ovs.sh, which sorts before this
# one) creates the in-guest bridge `phenix` and a `sniffer` GRE port. The stock
# bridge learns the mirrored MACs on `sniffer` and then drops frames for them,
# so only the first flooded frames reach the capture port. This flow sends
# everything that arrives on `sniffer` to the bridge's local port and brings
# the link up. It goes away once the mirror app or the cm-malcolm image does it
# (DESIGN.md 16).
for _ in $(seq 1 60); do
  ovs-vsctl br-exists phenix 2> /dev/null && break
  sleep 2
done
ovs-ofctl add-flow phenix "priority=1000,in_port=sniffer,actions=output:phenix"
ip link set phenix up
