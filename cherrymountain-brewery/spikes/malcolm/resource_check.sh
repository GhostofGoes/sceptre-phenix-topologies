#!/usr/bin/env bash
# Run on the phenix/minimega HOST before and during a Malcolm VM run.
# Reports what is free and whether Malcolm's request (default 24 GB RAM,
# 8 vCPU) fits alongside the planned topology. Usage: MALCOLM_GB=24 ./resource_check.sh
set -euo pipefail

MALCOLM_GB="${MALCOLM_GB:-24}"
OTHER_VMS_GB="${OTHER_VMS_GB:-60}"   # estimate: rest of the range, DESIGN §13 totals
total_gb=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
avail_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo)
cpus=$(nproc)
echo "host: ${total_gb} GB total, ${avail_gb} GB available, ${cpus} CPUs"
need=$(( MALCOLM_GB + OTHER_VMS_GB ))
echo "need ~${need} GB (malcolm ${MALCOLM_GB} + rest ${OTHER_VMS_GB}), leave 10% headroom"
echo "swap in use (MB): $(free -m | awk '/^Swap:/ {print $3}')"
echo "KSM sharing: $(cat /sys/kernel/mm/ksm/run 2>/dev/null || echo n/a) (1 = on)"
if command -v minimega >/dev/null; then
  echo "minimega vms:"; minimega -e vm info 2>/dev/null | head -20 || true
fi
if (( total_gb * 9 / 10 >= need )); then echo "FIT: yes (${need} <= 90% of ${total_gb})"; else
  echo "FIT: NO. Use the lite Malcolm variant or trim the range"; exit 1; fi
