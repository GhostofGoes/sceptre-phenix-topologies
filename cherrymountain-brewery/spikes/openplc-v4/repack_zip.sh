#!/usr/bin/env bash
# Repack an Editor-built program ZIP with per-node plugin configs (design §12.1
# step 3). Usage: ./repack_zip.sh in.zip out.zip conf_dir [member_dir]
#   conf_dir   directory with plugin JSON files, e.g. opcua.json modbus_master.json
#   member_dir directory inside the ZIP that holds plugin configs (default: conf)
# Verified at v4.3.2: configs live in conf/ inside the ZIP and the file name
# (without .json) must equal the plugin name in plugins.conf (opcua,
# modbus_slave, modbus_master, s7comm). webserver/plcapp_management.py
# update_plugin_configurations() scans core/generated/conf/*.json, copies each
# match to the plugin directory, and DISABLES every plugin with no file; a ZIP
# with no conf/ directory disables all plugins.
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
