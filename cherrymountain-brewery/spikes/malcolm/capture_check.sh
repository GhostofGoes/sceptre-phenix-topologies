#!/usr/bin/env bash
# Verify mirror capture reaches Malcolm. Run in three places (see README):
#   ./capture_check.sh gen   gen-a   -> generate known traffic to gen-b
#   ./capture_check.sh ovs   malcolm -> show the in-guest OVS mirror port
#   ./capture_check.sh seen  malcolm -> count packets arriving on the capture NIC
# Env: TARGET=10.20.30.31 (gen-b), IFACE= (capture interface on malcolm)
set -euo pipefail

mode="${1:?gen|ovs|seen}"
TARGET="${TARGET:-10.20.30.31}"
case "${mode}" in
  gen)
    # Distinctive traffic: Modbus-looking TCP 502 probes plus ICMP and HTTP.
    ping -c 5 "${TARGET}" || true
    for i in $(seq 1 10); do
      timeout 2 bash -c "echo -n probe-${i} > /dev/tcp/${TARGET}/502" 2>/dev/null || true
    done
    echo "generated: 5 ICMP + 10 TCP/502 attempts toward ${TARGET}"
    ;;
  ovs)
    # setupOVS: true should create an OVS bridge in the guest. TODO(verify):
    # bridge/port names the mirror app creates; compare with `ovs-vsctl show`.
    ovs-vsctl show
    ovs-vsctl list-ports "$(ovs-vsctl list-br | head -1)"
    ip -br link
    ;;
  seen)
    IFACE="${IFACE:-$(ovs-vsctl list-br | head -1)}"
    echo "capturing 15 s on ${IFACE}"
    timeout 15 tcpdump -nn -i "${IFACE}" -c 200 'icmp or tcp port 502' 2>&1 | tail -15
    # Then in Malcolm: Arkime session search for ip.dst == ${TARGET}, and
    # Dashboards Zeek conn log. Record whether both directions appear once.
    ;;
  *) echo "unknown mode ${mode}"; exit 2 ;;
esac
