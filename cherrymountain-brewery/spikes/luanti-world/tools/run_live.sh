#!/usr/bin/env bash
# One live session: the spike harness (cm-viz offline harness + control relay)
# and the Luanti client following a tour.
# usage: run_live.sh OUTDIR CONF SCENARIO SPEED TIMEOUT_S [FAST_FORWARD_S]
# PYTHON must have cherrymountain-viz[local] and cherrymountain-sim installed.
set -euo pipefail
out=$(realpath -m "$1"); conf=$2; scenario=$3; speed=$4; timeout_s=$5; ff=${6:-0}
here=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-python3}
mkdir -p "$out"
"$py" "$here/harness.py" --helics "$here/../../plant/generated/full/helics.json" \
  --scenario "$scenario" --speed "$speed" --fast-forward "$ff" > "$out/harness.log" 2>&1 &
hp=$!
trap 'kill $hp 2>/dev/null || true' EXIT
for _ in $(seq 60); do curl -sf localhost:8090/frame.json >/dev/null && break; sleep 1; done
"$here/tools/shoot_tour.sh" "$out" "$conf" "$timeout_s"
curl -s localhost:8092/control > "$out/relay-log.json" || true
