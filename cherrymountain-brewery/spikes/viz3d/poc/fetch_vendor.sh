#!/usr/bin/env bash
# Fetch the pinned three.js files the viz3d spike pages import, into web/vendor/.
#
# three.js is MIT-licensed; its LICENSE is copied next to the files. The
# tarball is pinned by version and SHA-256, so a changed upstream file fails the
# check instead of loading. web/vendor/ is not committed (.gitignore).
set -euo pipefail

THREE_VERSION=0.186.1
THREE_TGZ_SHA256=8cd068708ea44f2c73c944b1cead2ba2f0d5c15c8fc194e5700f4e4f4a033fe7
URL="https://registry.npmjs.org/three/-/three-${THREE_VERSION}.tgz"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
vendor="${here}/web/vendor"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

curl -fsSL -o "${work}/three.tgz" "${URL}"
echo "${THREE_TGZ_SHA256}  ${work}/three.tgz" | sha256sum -c --quiet -

tar -xzf "${work}/three.tgz" -C "${work}" \
  package/LICENSE \
  package/build/three.module.js \
  package/build/three.core.js \
  package/examples/jsm/controls/OrbitControls.js \
  package/examples/jsm/exporters/GLTFExporter.js \
  package/examples/jsm/loaders/GLTFLoader.js \
  package/examples/jsm/utils/BufferGeometryUtils.js \
  package/examples/jsm/utils/SkeletonUtils.js

rm -rf "${vendor}"
mkdir -p "${vendor}/addons"
cp "${work}/package/LICENSE" "${vendor}/LICENSE-three.txt"
cp "${work}/package/build/three.module.js" "${work}/package/build/three.core.js" "${vendor}/"
cp -r "${work}/package/examples/jsm/controls" "${work}/package/examples/jsm/exporters" \
  "${work}/package/examples/jsm/loaders" "${work}/package/examples/jsm/utils" "${vendor}/addons/"
echo "three.js ${THREE_VERSION} -> ${vendor}"
