#!/usr/bin/env bash
# Build the three ot-sim pieces a Modbus RTU node needs (message bus, CPU module,
# Modbus module, plus the logic module for a ramp) on an ubuntu-24.04 host, and
# pack them with the shared libraries they link into one tarball that a guest
# unpacks under /opt/otsim. The cm-spike guests have none of these libraries and
# no network, so everything travels in the tarball (phenix injects it at start).
#
#   OTSIM_REF=<commit> OUT=/phenix/injects/otsim-rtu.tgz ./build_otsim.sh
#
# The pin is the commit the otsim-noble spike built and ran. Needs apt packages
# build-essential, libczmq-dev, libzmq3-dev, libxml2-dev, pkg-config and Go 1.22+.
set -euo pipefail

OTSIM_REPO="${OTSIM_REPO:-https://github.com/patsec/ot-sim}"
OTSIM_REF="${OTSIM_REF:-9dc33047ff9fb474e61c8931c7dac7bf98df38ef}"
OUT="${OUT:-/phenix/injects/otsim-rtu.tgz}"
WORK="${WORK:-$(mktemp -d)}"

mkdir -p "$WORK/src" "$WORK/pkg/otsim/bin" "$WORK/pkg/otsim/lib"
cd "$WORK/src"
git init -q .
git fetch -q --depth 1 "$OTSIM_REPO" "$OTSIM_REF"
git checkout -q FETCH_HEAD
echo "ot-sim $(git rev-parse HEAD)"

# The message bus is one C file (czmq and libxml2); no submodules or CMake needed.
# shellcheck disable=SC2046
gcc -O2 -o "$WORK/pkg/otsim/bin/ot-sim-message-bus" src/c/cmd/ot-sim-message-bus/main.c \
  $(pkg-config --cflags libxml-2.0) -lczmq -lzmq $(pkg-config --libs libxml-2.0)

# The Go modules (cgo against libzmq). RTU is in goburrow/serial through actions/modbus.
cd "$WORK/src/src/go"
for module in cpu modbus logic; do
  GOOS=linux go build -ldflags="-s -w" -trimpath -o "$WORK/pkg/otsim/bin/ot-sim-$module-module" "cmd/ot-sim-$module-module/main.go"
done

# Shared libraries the binaries need that a minimal noble guest does not have.
for bin in "$WORK"/pkg/otsim/bin/*; do
  ldd "$bin"
done | awk '/=> \// { print $3 }' | sort -u | grep -vE '/(libc|libm|libdl|libpthread|librt|libresolv|libgcc_s|libstdc\+\+)\.so' |
  while read -r lib; do cp -L "$lib" "$WORK/pkg/otsim/lib/"; done

mkdir -p "$(dirname "$OUT")"
tar -C "$WORK/pkg" -czf "$OUT" otsim
ls -lh "$OUT"
tar -tzf "$OUT" | head -40
echo "built $OUT"
