#!/usr/bin/env bash
# shellcheck shell=bash
# Run ON a VyOS router, as a member of vyattacfg:
#   sg vyattacfg -c 'vbash ./vyos_commit_check.sh generated.sh'
# Applies the `set` and `delete` lines of a script one by one, names every line
# VyOS refuses, and then commits what it took. vyos_ci.py runs it on a router
# that did not take phenix's rendered configuration at boot, to find out why.
# VyOS's script-template is not safe under -e or -u, so only pipefail here.
set -o pipefail

# VyOS's script-template resets the positional parameters, so keep them first.
ARGS=("$@")

# shellcheck disable=SC1091
source /opt/vyatta/etc/functions/script-template

[ -r "${ARGS[0]:?the generated script}" ] || { echo "cannot read ${ARGS[0]}"; exit 2; }

configure
n=0
bad=0
while IFS= read -r line; do
  n=$((n + 1))
  # `set` and `delete` are aliases in a VyOS script, so each line goes through eval.
  if ! eval "${line}" >/tmp/vyos-set.out 2>&1; then
    bad=$((bad + 1))
    echo "REFUSED: ${line}"
    sed 's/^/    /' /tmp/vyos-set.out
  fi
done < <(grep -E '^(set|delete) ' "${ARGS[0]}")
echo "applied ${n} commands, ${bad} refused; committing"
if commit >/tmp/vyos-commit.out 2>&1; then
  cat /tmp/vyos-commit.out
  if [ "${bad}" -eq 0 ]; then
    save >/dev/null 2>&1
    echo "PASS: commit succeeded"
  else
    echo "FAIL: commit succeeded without the ${bad} refused commands"
  fi
else
  cat /tmp/vyos-commit.out
  echo "FAIL: commit rejected"
  discard
fi
