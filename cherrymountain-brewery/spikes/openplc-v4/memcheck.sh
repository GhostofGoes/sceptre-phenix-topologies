#!/usr/bin/env bash
# Run ON the PLC VM while opcua + modbus_master are active and a compile is
# running. Samples memory every 2 s for DURATION seconds and prints peak use.
# Usage: DURATION=300 ./memcheck.sh   (start a compile from another shell)
set -euo pipefail

DURATION="${DURATION:-300}"
peak=0
end=$(( $(date +%s) + DURATION ))
free -m | head -2
while (( $(date +%s) < end )); do
  used=$(free -m | awk '/^Mem:/ {print $3}')
  (( used > peak )) && peak=${used}
  sleep 2
done
echo "peak_used_mb=${peak}"
echo "swap:"; free -m | awk '/^Swap:/'
echo "top RSS:"; ps -eo rss,comm --sort=-rss | head -8
# Compare peak_used_mb against the VM memory in the topology (design table).
