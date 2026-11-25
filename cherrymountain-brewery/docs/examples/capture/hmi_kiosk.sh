#!/usr/bin/env bash
# Capture an HMI station's screen: the range's own cm-kiosk script runs Chromium
# in kiosk mode on a FUXA view, signed in as the FUXA operator account, on a
# virtual 1440x900 X display (the size of the range's desktops, with no window
# manager). The picture is the X display itself.
#
#   hmi_kiosk.sh VIEW OUT.png
#
# Needs a running scada-fuxa (fuxa_run.sh up scada ...), Xvfb, ImageMagick's
# `import`, Python 3.12, and Node with Playwright (only to find its Chromium).
# Environment: CM_ROOT, WORK (as in fuxa_run.sh), FUXA_PORT (default 11881),
# OPERATOR_PW (default: the one fuxa_run.sh sets), CHROME (default: Playwright's),
# XDISPLAY (the X display number to start Xvfb on; default 98).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CM_ROOT=${CM_ROOT:-$(cd "${HERE}/../../.." && pwd)}
WORK=${WORK:-/tmp/cm-examples}
PORT=${FUXA_PORT:-11881}
OPERATOR_PW=${OPERATOR_PW:-Oper8tor-example-pw}
PY=${PYTHON:-python3}
VIEW=$1
OUT=$2
DISPLAY_NUM=${XDISPLAY:-98}
STATE="${WORK}/kiosk-state"
KIOSK="${CM_ROOT}/apps/cherrymountain_apps/cherrymountain/templates/scripts/cm-kiosk"
CHROME=${CHROME:-$(node -e "console.log(require('playwright').chromium.executablePath())")}

mkdir -p "${WORK}"
rm -rf "${STATE:?}"
# The image's Chromium is launched by a script that sets the range's flags; this
# machine runs as root without a sandbox, so the wrapper adds --no-sandbox, and
# with no window manager to maximize the window it sets the window to the screen.
printf '#!/bin/sh\nexec "%s" --no-sandbox --disable-gpu --window-size=1440,900 --window-position=0,0 "$@"\n' "${CHROME}" > "${WORK}/chrome-wrap.sh"
chmod +x "${WORK}/chrome-wrap.sh"
cat > "${WORK}/kiosk.env" <<EOF
KIOSK_URL=http://127.0.0.1:${PORT}/home?viewName=${VIEW// /%20}
KIOSK_BROWSER=${WORK}/chrome-wrap.sh
EOF
( umask 077; printf 'KIOSK_USER=operator\nKIOSK_PASSWORD=%s\n' "${OPERATOR_PW}" > "${WORK}/kiosk-cred.env" )

Xvfb ":${DISPLAY_NUM}" -screen 0 1440x900x24 -nolisten tcp > "${WORK}/xvfb.log" 2>&1 &
XPID=$!
cleanup() {
  [ -n "${KPID:-}" ] && kill "${KPID}" 2>/dev/null || true
  sleep 1
  pkill -f "chrome.*${STATE}" 2>/dev/null || true
  kill "${XPID}" 2>/dev/null || true
}
trap cleanup EXIT
sleep 2
DISPLAY=":${DISPLAY_NUM}" "${PY}" "${KIOSK}" --env "${WORK}/kiosk.env" --credentials "${WORK}/kiosk-cred.env" \
  --state-dir "${STATE}" > "${WORK}/kiosk.log" 2>&1 &
KPID=$!

# The kiosk writes `state=operator` once it has signed in.
for _ in $(seq 1 60); do
  grep -q 'state=operator' "${STATE}/status" 2>/dev/null && break
  sleep 1
done
grep -q 'state=operator' "${STATE}/status" || { cat "${STATE}/status" "${WORK}/kiosk.log" >&2; echo "kiosk did not sign in" >&2; exit 1; }
sleep 10 # values arrive and the view settles
DISPLAY=":${DISPLAY_NUM}" import -window root "${OUT}"
echo "kiosk status: $(tr '\n' ' ' < "${STATE}/status")"
