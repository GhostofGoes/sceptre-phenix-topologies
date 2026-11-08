#!/usr/bin/env bash
# Build an OpenPLC v4 program ZIP without the Editor (spike fixture).
# Usage: STRUCPP=/path/to/built/strucpp ./build_zip.sh [out.zip]
# Layout verified against runtime v4.3.2 (scripts/compile.sh, Makefile.strucpp,
# webserver/plcapp_management.py): generated.cpp, generated.hpp,
# generated_debug.cpp, defines.h (PROGRAM_MD5), strucpp_runtime/include/ (the
# strucpp runtime headers, shipped with every upload), conf/*.json.
set -euo pipefail

: "${STRUCPP:?set STRUCPP to a built strucpp checkout (npm ci && npm run build)}"
here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "${1:-prog.zip}")
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

node "${here}/gen_program.mjs" "${STRUCPP}" "${here}/fixture/prog.st" "${work}"
rm "${work}/debug_map.json"
mkdir -p "${work}/strucpp_runtime"
cp -r "${STRUCPP}/src/runtime/include" "${work}/strucpp_runtime/include"
printf '#pragma once\n#define PROGRAM_MD5 "spike"\n' > "${work}/defines.h"
base="${work}/../$(basename "${work}").base.zip"
(cd "${work}" && zip -qr "${base}" .)
"${here}/repack_zip.sh" "${base}" "${out}" "${here}/fixture/conf" conf >/dev/null
rm -f "${base}"
echo "wrote ${out}"
