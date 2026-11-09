#!/usr/bin/env bash
# Produce the spike evidence: measurements and screenshots in ../evidence/.
#
#   PYTHON=/path/to/venv/bin/python ./run_evidence.sh
#
# Needs: the venv of README.md (sim + websockets), node with the playwright
# package, taskset, xvfb-run, and ImageMagick's convert (screenshots are
# quantized to 256 colors to stay small). Takes about six minutes.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
evidence="${here}/../evidence"
PYTHON="${PYTHON:-python3}"
shots="$(mktemp -d)"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "${pid}" 2>/dev/null || true; done
  rm -rf "${shots}"
}
trap cleanup EXIT
mkdir -p "${evidence}"
rm -f "${evidence}"/*.png "${evidence}"/results-*.json
cd "${here}"
[[ -f web/vendor/three.module.js ]] || ./fetch_vendor.sh

# start_bridge PORT ARGS...: run a bridge and wait until it answers.
start_bridge() {
  local port="$1"
  shift
  "${PYTHON}" bridge.py --port "${port}" --log-level WARNING "$@" &
  pids+=("$!")
  for _ in $(seq 60); do
    curl -fsS "http://127.0.0.1:${port}/stats.json" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "bridge on ${port} did not start" >&2
  return 1
}
measure() { node measure.cjs --shots "${shots}" "$@"; }

# 1. The worst case for the renderer: a CO2 release with the purge fans stopped
#    by a malicious BACnet write, 20 simulated minutes in, at real time.
start_bridge 8090 --source local --scenario co2-attack --fast-forward 1200 --speed 1
base=http://127.0.0.1:8090
measure --base "${base}" --label 4cpu --prefix attack- --out "${evidence}/results-4cpu.json" \
  --cases webgl-720,webgl-1080,webgl-720-fps15,webgl-720-aa,webgl-no-swiftshader-flag,svg-720,fanout-20
taskset -c 0,1 node measure.cjs --base "${base}" --label 2cpu --out "${evidence}/results-2cpu.json" \
  --cases webgl-720,webgl-720-fps15,svg-720
taskset -c 0 node measure.cjs --base "${base}" --label 1cpu --out "${evidence}/results-1cpu.json" \
  --cases webgl-720,webgl-720-fps15
# Headed Chromium on an X server with Mesa, as on the cm-desktop VMs.
xvfb-run -a -s "-screen 0 1920x1080x24" node measure.cjs --base "${base}" --label headed-xvfb-4cpu \
  --shots "${shots}" --out "${evidence}/results-headed-4cpu.json" \
  --cases headed-default-720,headed-llvmpipe-720,headed-no-swiftshader-flag,headed-cm-desktop-flags,headed-cm-desktop-flags-swiftshader
taskset -c 0,1 xvfb-run -a -s "-screen 0 1920x1080x24" node measure.cjs --base "${base}" \
  --label headed-xvfb-2cpu --out "${evidence}/results-headed-2cpu.json" \
  --cases headed-default-720,headed-llvmpipe-720,headed-llvmpipe-720-fps15

# 2. The same leak with the purge fans working, and the plant in normal running.
start_bridge 8091 --source local --scenario co2-leak --fast-forward 1200 --speed 1
measure --base http://127.0.0.1:8091 --label leak --prefix leak- --seconds 4 \
  --out "${evidence}/results-leak.json" --cases webgl-720
start_bridge 8092 --source local --scenario none --fast-forward 300 --speed 1
measure --base http://127.0.0.1:8092 --label normal --prefix normal- --seconds 4 \
  --out "${evidence}/results-normal.json" --cases webgl-720,svg-720

# 3. Screenshots: 256 colors, PNG.
for png in "${shots}"/*.png; do
  convert "${png}" -colors 256 "PNG8:${evidence}/$(basename "${png}")"
done
ls -l "${evidence}"
