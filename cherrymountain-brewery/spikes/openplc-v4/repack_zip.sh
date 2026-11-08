#!/usr/bin/env bash
# Repack an Editor-built program ZIP with per-node plugin configs (design §12.1
# step 3). Usage: ./repack_zip.sh in.zip out.zip conf_dir [member_dir]
#   conf_dir   directory with plugin JSON files, e.g. opcua.json modbus_master.json
#   member_dir directory inside the ZIP that holds plugin configs (default: conf)
# TODO(verify): the in-ZIP location of plugin configs. Build a project with
# plugins enabled in the Editor, `unzip -l` it, and set member_dir to match.
set -euo pipefail

in="${1:?input zip}"
out="${2:?output zip}"
confdir="${3:?plugin config directory}"
member="${4:-conf}"

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
unzip -q "${in}" -d "${work}"
echo "original contents:"
(cd "${work}" && find . -type f | sort)
mkdir -p "${work}/${member}"
cp -v "${confdir}"/*.json "${work}/${member}/"
out_abs=$(realpath -m "${out}")
rm -f "${out_abs}"
(cd "${work}" && zip -qr "${out_abs}" .)
echo "wrote ${out_abs}"
unzip -l "${out_abs}"
