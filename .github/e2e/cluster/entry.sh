#!/bin/bash
# Start this node's Open vSwitch, then run minimega in the foreground with the
# head's port and -filepath, and a base directory of its own.
set -euo pipefail
/usr/share/openvswitch/scripts/ovs-ctl start --system-id=random
for _ in $(seq 30); do
    ovs-vsctl show >/dev/null 2>&1 && break
    sleep 1
done
ovs-vsctl show
mkdir -p "$MM_BASE"
exec /usr/local/bin/minimega -nostdin -force -base "$MM_BASE" \
    -filepath /phenix/images -abssnapshot -degree 0 -port 9000 \
    -context minimega -level info -logfile "$MM_BASE/minimega.log"
