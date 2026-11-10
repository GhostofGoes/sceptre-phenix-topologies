#!/bin/bash
# phenix startup hook on scada-fuxa and bas-fuxa: the page title of FUXA's client.
# FUXA's client is a built Angular app: its index.html names the page "FUXA" and no
# project or server setting changes that (client/dist/index.html at the cm-fuxa pin;
# frangoteam/FUXA has no title setting at b2972583). The tab icon (favicon.ico) and the
# mark (server/_images/Brand/icon.svg) are injected as files over the ones FUXA ships or
# next to them, so this is the only thing that has to edit a file.
#
# Env (from /etc/default/cm-fuxa): FUXA_TITLE, the title to write (letters, digits,
# spaces, dots, dashes, and underscores; default "CherryMountain Brewery"). FUXA_INDEX
# is for tests: the file to edit. Safe to run again: the new title replaces the old.
set -euo pipefail

if [ -f /etc/default/cm-fuxa ]; then
  # shellcheck source=/dev/null
  . /etc/default/cm-fuxa
fi
INDEX="${FUXA_INDEX:-/opt/fuxa/client/dist/index.html}"
TITLE="${FUXA_TITLE:-CherryMountain Brewery}"

case "$TITLE" in
  '' | *[!A-Za-z0-9\ ._-]*)
    echo "cm-fuxa-brand: FAIL: FUXA_TITLE must be letters, digits, spaces, dots, dashes, or underscores" >&2
    exit 1
    ;;
esac
if [ ! -f "$INDEX" ]; then
  echo "cm-fuxa-brand: FAIL: no ${INDEX}" >&2
  exit 1
fi

sed -i "s|<title>[^<]*</title>|<title>${TITLE}</title>|" "$INDEX"
grep -qF "<title>${TITLE}</title>" "$INDEX" || {
  echo "cm-fuxa-brand: FAIL: ${INDEX} has no <title> to set" >&2
  exit 1
}
echo "cm-fuxa-brand: page title is ${TITLE}"
