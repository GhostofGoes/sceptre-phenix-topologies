#!/usr/bin/env bash
# Build the OpenPLC v4 program ZIP of one PLC for the full or lite variant,
# without the OpenPLC Editor (DESIGN.md 12.1). The ci slice's plc-cellar ZIP has
# its own builder, plc/plc-cellar/build_zip.sh.
#
#   plc/<plc>/<program>.st + located_vars.st + alarms.json --plcbuild.py--> one ST file
#   one ST file --strucpp (pinned)--> generated.cpp / .hpp / generated_debug.cpp / debug_map.json
#   + defines.h (PROGRAM_MD5) + strucpp runtime headers + conf/*.json  --> ZIP
#
# Output: phenix-injects/openplc/<variant>/<plc>.zip (Git LFS). The build is
# reproducible: the same inputs give the same entries.
#
# Usage:
#   plc/common/build_zip.sh PLC [--variant full|lite] [--check]
#   plc/common/build_zip.sh --all [--check]      every PLC, both variants
# Environment: STRUCPP and OUT as in plc/plc-cellar/build_zip.sh.
set -euo pipefail

STRUCPP_REF=fcccfbdf53704fad1f5e71dd2a05d440b9028b47
STRUCPP_URL=https://github.com/Autonomy-Logic/strucpp.git
PLCS=(plc-brewhouse plc-cellar plc-packaging plc-utilities plc-power)

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "${here}/../.." && pwd)

usage() { echo "usage: $0 PLC [--variant full|lite] [--check] | $0 --all [--check]" >&2; exit 2; }

plc="" variant=full check=0 all=0
while [ $# -gt 0 ]; do
  case "$1" in
    --variant) [ $# -ge 2 ] || usage; variant=$2; shift 2 ;;
    --check) check=1; shift ;;
    --all) all=1; shift ;;
    -*) usage ;;
    *) [ -z "${plc}" ] || usage; plc=$1; shift ;;
  esac
done
case "${variant}" in full|lite) ;; *) usage ;; esac

if [ "${all}" = 1 ]; then
  [ -z "${plc}" ] || usage
  rc=0
  for v in full lite; do
    for p in "${PLCS[@]}"; do
      args=("${p}" --variant "${v}")
      [ "${check}" = 0 ] || args+=(--check)
      "$0" "${args[@]}" || rc=1
    done
  done
  exit "${rc}"
fi
[ -n "${plc}" ] || usage
[ -d "${root}/plc/${plc}" ] || { echo "no plc/${plc} directory" >&2; exit 2; }

log() { echo "build_zip ${plc} (${variant}): $*"; }

ensure_strucpp() {
  local dir="${STRUCPP:-${XDG_CACHE_HOME:-${HOME}/.cache}/cherrymountain/strucpp-${STRUCPP_REF:0:12}}"
  if [ ! -d "${dir}/.git" ]; then
    [ -z "${STRUCPP:-}" ] || { echo "STRUCPP=${STRUCPP} is not a git checkout" >&2; exit 1; }
    log "cloning strucpp ${STRUCPP_REF:0:12} into ${dir}"
    mkdir -p "$(dirname "${dir}")"
    rm -rf "${dir}.tmp"
    git clone --quiet "${STRUCPP_URL}" "${dir}.tmp"
    git -C "${dir}.tmp" checkout --quiet "${STRUCPP_REF}"
    mv "${dir}.tmp" "${dir}"
  fi
  local have
  have=$(git -C "${dir}" rev-parse HEAD)
  if [ "${have}" != "${STRUCPP_REF}" ]; then
    echo "strucpp at ${dir} is ${have}, expected ${STRUCPP_REF}" >&2
    exit 1
  fi
  if [ ! -f "${dir}/dist/node/index.js" ] || [ ! -f "${dir}/libs/iec-standard-fb.stlib" ]; then
    log "building strucpp (npm ci && npm run build)"
    (cd "${dir}" && npm ci --silent && npm run --silent build)
  fi
  STRUCPP_DIR="${dir}"
}

out=$(realpath -m "${OUT:-${root}/phenix-injects/openplc/${variant}/${plc}.zip}")
conf_dir="${root}/plant/generated/${variant}/openplc/${plc}"
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

ensure_strucpp
python3 -I "${here}/plcbuild.py" "${plc}" --variant "${variant}" --out "${work}/asm" >/dev/null
program="${work}/asm/${plc}-full.st"
md5=$(md5sum "${program}" | cut -d' ' -f1)
log "PROGRAM_MD5=${md5}"
node "${here}/gen_program.mjs" "${STRUCPP_DIR}" "${program}" "${work}/gen" "${md5}" >/dev/null

target="${out}"
[ "${check}" = 0 ] || target="${work}/${plc}.zip"
python3 -I "${here}/pack_zip.py" \
  --generated "${work}/gen" \
  --runtime-include "${STRUCPP_DIR}/src/runtime/include" \
  --conf "${conf_dir}" \
  --md5 "${md5}" \
  --out "${target}" >/dev/null

if [ "${check}" = 0 ]; then
  log "wrote ${out#"${root}"/}"
  exit 0
fi
rc=0
python3 -I "${root}/plc/common/zipdiff.py" "${target}" "${out}" || rc=$?
case "${rc}" in
  0) log "PASS: ${out#"${root}"/} is up to date" ;;
  1) log "FAIL: ${out#"${root}"/} differs from a fresh build (entries above); run $0 ${plc} --variant ${variant}" >&2; exit 1 ;;
  *) log "FAIL: cannot compare with ${out#"${root}"/}" >&2; exit "${rc}" ;;
esac
