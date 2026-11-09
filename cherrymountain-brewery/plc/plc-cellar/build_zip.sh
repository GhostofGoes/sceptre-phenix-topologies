#!/usr/bin/env bash
# Build the OpenPLC v4 program ZIP for plc-cellar without the OpenPLC Editor.
#
#   plc-cellar.st + plantgen located_vars.st  --assemble.py-->  one ST file
#   one ST file --strucpp (pinned)--> generated.cpp / .hpp / generated_debug.cpp
#   + defines.h (PROGRAM_MD5) + strucpp runtime headers + conf/*.json  --> ZIP
#
# Output: phenix-injects/openplc/plc-cellar.zip (a Git LFS file). The runtime
# (v4.3.x is strucpp based) compiles the C++ on the PLC at upload time, so the
# ZIP holds sources, not binaries. The build is reproducible: the same inputs
# give the same bytes (pack_zip.py fixes timestamps and entry order).
#
# Usage:
#   plc/plc-cellar/build_zip.sh            # build and write the ZIP
#   plc/plc-cellar/build_zip.sh --check    # rebuild to a temp file, fail if the committed ZIP differs
# Environment:
#   STRUCPP      a built checkout of Autonomy-Logic/strucpp at STRUCPP_REF
#                (default: a cached clone under $XDG_CACHE_HOME/cherrymountain,
#                made on first use with `npm ci && npm run build`; needs git,
#                node >= 22, and npm)
#   OUT          output ZIP path (default: the committed path above)
# Run plantgen (python -m plantgen) and sync_ci.py first: conf/ is read from
# plant/generated/ci/openplc/plc-cellar/ and the program from located_vars.st.
set -euo pipefail

# strucpp commit used for every ZIP (package 0.7.0). The compiler and its
# runtime headers must come from the same commit; both are taken from it.
STRUCPP_REF=fcccfbdf53704fad1f5e71dd2a05d440b9028b47
STRUCPP_URL=https://github.com/Autonomy-Logic/strucpp.git

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "${here}/../.." && pwd)
variant=ci
conf_dir="${root}/plant/generated/${variant}/openplc/plc-cellar"
out=$(realpath -m "${OUT:-${root}/phenix-injects/openplc/plc-cellar.zip}")
check=0
case "${1:-}" in
  "") ;;
  --check) check=1 ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

log() { echo "build_zip: $*"; }

ensure_strucpp() {
  local dir="${STRUCPP:-${XDG_CACHE_HOME:-${HOME}/.cache}/cherrymountain/strucpp-${STRUCPP_REF:0:12}}"
  if [ ! -d "${dir}/.git" ]; then
    [ -z "${STRUCPP:-}" ] || { echo "STRUCPP=${STRUCPP} is not a git checkout" >&2; exit 1; }
    log "cloning strucpp ${STRUCPP_REF:0:12} into ${dir}"
    mkdir -p "$(dirname "${dir}")"
    # Clone and check out beside the cache directory, then move it into place, so
    # an interrupted clone never leaves a checkout at the wrong commit there.
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

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

ensure_strucpp
log "assembling the program"
python3 -I "${here}/assemble.py" --variant "${variant}" --out "${work}/asm"
program="${work}/asm/plc-cellar-full.st"
md5=$(md5sum "${program}" | cut -d' ' -f1)
log "PROGRAM_MD5=${md5}"

log "compiling with strucpp ${STRUCPP_REF:0:12}"
node "${here}/gen_program.mjs" "${STRUCPP_DIR}" "${program}" "${work}/gen" "${md5}"

target="${out}"
[ "${check}" = 0 ] || target="${work}/plc-cellar.zip"
python3 -I "${here}/pack_zip.py" \
  --generated "${work}/gen" \
  --runtime-include "${STRUCPP_DIR}/src/runtime/include" \
  --conf "${conf_dir}" \
  --md5 "${md5}" \
  --out "${target}"

if [ "${check}" = 1 ]; then
  # Compare entry names and contents, not raw bytes: deflate output can differ
  # between zlib builds while the archive holds the same files.
  # Exit 0 when they match, 1 (listing the entries that differ) when they do not,
  # and 3 when the committed file is not a ZIP (for example a Git LFS pointer:
  # run `git lfs pull` rather than rebuilding over it).
  rc=0
  python3 -I -c '
import hashlib, sys, zipfile
def entries(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}
fresh = entries(sys.argv[1])
try:
    committed = entries(sys.argv[2])
except zipfile.BadZipFile:
    with open(sys.argv[2], "rb") as fh:
        head = fh.read(64)
    kind = "a Git LFS pointer; run git lfs pull" if head.startswith(b"version https://git-lfs") else "not a ZIP"
    print(f"build_zip: {sys.argv[2]} is {kind}", file=sys.stderr)
    sys.exit(3)
if fresh == committed:
    sys.exit(0)
md5 = lambda b: hashlib.md5(b).hexdigest()[:12] if b is not None else "-"
for name in sorted(set(fresh) | set(committed)):
    a, b = fresh.get(name), committed.get(name)
    if a != b:
        print(f"build_zip:   {name}: fresh {md5(a)} committed {md5(b)}", file=sys.stderr)
sys.exit(1)
' "${target}" "${out}" || rc=$?
  case "${rc}" in
    0) log "PASS: ${out#"${root}"/} is up to date" ;;
    1) log "FAIL: ${out#"${root}"/} differs from a fresh build (entries above); run $0" >&2; exit 1 ;;
    *) log "FAIL: cannot compare with ${out#"${root}"/}" >&2; exit "${rc}" ;;
  esac
fi
