#!/usr/bin/env bash
# From a host behind one router, check the tunnel carries traffic, then DNP3.
# Usage (on utility-derms): ./tunnel_check.sh 10.20.15.10
# Needs ../otsim-noble/dnp3_link_probe.py next to or above this script, and an
# ot-sim outstation (or `nc -l 20000`) listening on the remote host.
set -euo pipefail

REMOTE="${1:?remote host (rtu-der) address}"
here=$(cd "$(dirname "$0")" && pwd)
probe="${here}/../otsim-noble/dnp3_link_probe.py"

echo "== ICMP across tunnel"
ping -c 3 -W 2 "${REMOTE}"
echo "== TCP 20000 reachable"
timeout 5 bash -c "exec 3<>/dev/tcp/${REMOTE}/20000" && echo "open"
echo "== DNP3 link status"
python3 "${probe}" "${REMOTE}"
echo "== check on the wire (run on the router): ESP, not cleartext"
echo "   sudo tcpdump -ni eth0 'esp or udp port 500 or udp port 4500' -c 10"
echo "PASS: ICMP, TCP 20000, and DNP3 link status work across the tunnel"
