#!/usr/bin/env bash
# Build one spike image on a CI runner prepared by .github/actions/e2e-setup
# (phenix, minimega and, for cm-spike, the vmdb2 image builder installed).
#   build_image.sh cm-spike   headless noble guest with the spike tools
#   build_image.sh vyos       VyOS 1.5 rolling, via the images repository's script
# Environment: IMAGES_DIR (checkout of sceptre-phenix-images), OUT_DIR.
# The image is left at $OUT_DIR/<name>.qc2.
set -euo pipefail

image="${1:?image name}"
here=$(cd "$(dirname "$0")" && pwd)
images_dir=$(cd "${IMAGES_DIR:?}" && pwd)
mkdir -p "${OUT_DIR:?}"
out=$(cd "${OUT_DIR}" && pwd)
start=$(date +%s)

case "$image" in
cm-spike)
  phenix=(sudo /usr/local/bin/phenix --store.endpoint bolt:///etc/phenix/store.bdb)
  sudo mkdir -p /etc/phenix
  packages=$(tr -d '[:space:]' <"$here/images/packages.txt")
  "${phenix[@]}" image create -r noble -v minbase -P "$packages" \
    -T "$here/images/cm-spike.sh" --compress cm-spike
  work=$(mktemp -d)
  (cd "$work" && "${phenix[@]}" image build -o "$work" -c -x cm-spike)
  "${phenix[@]}" image inject-miniexe /usr/local/bin/miniccc "$work/cm-spike.qc2"
  sudo mv "$work/cm-spike.qc2" "$out/cm-spike.qc2"
  ;;
vyos)
  # VyOS is not debootstrapped: the script repacks the release ISO as root and
  # injects miniccc through nbd, in a roomy temp directory. Microsoft's mirror
  # of the Docker Hub library images avoids the rate limits of shared runners.
  cp /usr/local/bin/miniccc /usr/local/bin/minirouter /usr/local/bin/protonuke "$images_dir/"
  # Older images refs (before f7b2a00) cannot parse vyos-build's new rolling image names
  # (vyos-999.<stamp>-qemu-amd64.qcow2) and still patch a NIC sort that vyos-1x dropped.
  # Patch only a script that still has the old code; a newer ref has both fixes.
  python3 - "$images_dir/scripts/vyos/build-vyos.sh" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
if "VERSION_DIR=\\$VERSION-rolling-\\$DATESTAMP" not in text:
    print("build-vyos.sh already handles the 999.<stamp> image names; not patching")
    raise SystemExit(0)
anchor = "mount /dev/nbd0p3 \\$MOUNT\n"
fix = (
    "VERSION_DIR=\\$(basename \\$(dirname \\$(ls \\$BOOT_PATH/*/*.squashfs | head -1)))\n"
    "SQUASHFS=\\$(basename \\$(ls \\$BOOT_PATH/*/*.squashfs | head -1))\n"
)
if text.count(anchor) != 1:
    raise SystemExit("build-vyos.sh no longer has the expected mount line; update build_image.sh")
text = text.replace(anchor, anchor + fix)
# vyos-1x orders first-boot NICs by numeric PCI address itself; warn instead of failing.
fail = 'so update build-vyos.sh" >&2\n    exit 1\n'
if text.count(fail) == 1:
    text = text.replace(fail, 'so update build-vyos.sh" >&2\n')
path.write_text(text)
PY
  grep -n 'VERSION_DIR=' "$images_dir/scripts/vyos/build-vyos.sh" || true
  (cd "$images_dir" && sudo -E env VYOSTMP=/mnt/vyostmp \
    INJECT_BASE_IMAGE=mcr.microsoft.com/mirror/docker/library/ubuntu:24.04 make vyos)
  sudo mv "$images_dir/vyos.qc2" "$out/vyos.qc2"
  ;;
*)
  echo "unknown image $image" >&2
  exit 2
  ;;
esac

sudo chown -R "$(id -u):$(id -g)" "$out"
echo "built $image in $(($(date +%s) - start)) s"
qemu-img info "$out/$image.qc2"
