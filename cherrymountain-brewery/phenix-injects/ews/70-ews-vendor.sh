#!/bin/bash
# phenix startup hook on the EWS (DESIGN.md 13). Creates the local account that
# Ridgeline's technician logs in as, from /etc/cherrymountain/ridgeline.env (injected,
# mode 0600; range-only values). The Guacamole connections ews-rdp and ews-ssh on
# dmz-jump carry the same account, so a session opens straight into an Xfce desktop
# (xrdp) or a shell (sshd). The account is an ordinary user: the vendor works through
# the engineering tools, and the EWS is where the audit trail of the vendor path is
# (sshd and xrdp-sesman log to auth.log, which the Wazuh agent reads).
#
# Re-running is safe: an existing account only gets its password set again.
set -euo pipefail

SETTINGS=/etc/cherrymountain/ridgeline.env
if [ ! -f "$SETTINGS" ]; then
  echo "70-ews-vendor: no ${SETTINGS}; no vendor account"
  exit 0
fi
# shellcheck source=/dev/null
. "$SETTINGS"
: "${VENDOR_EWS_USER:?set VENDOR_EWS_USER}" "${VENDOR_EWS_PASSWORD:?set VENDOR_EWS_PASSWORD}"
case "$VENDOR_EWS_USER" in
  *[!A-Za-z0-9._-]* | '' | -*)
    echo "70-ews-vendor: VENDOR_EWS_USER has a character that is not allowed" >&2
    exit 1
    ;;
esac

if ! id -u -- "$VENDOR_EWS_USER" > /dev/null 2>&1; then
  # -m copies /etc/skel, which holds the .xsession that starts Xfce for xrdp.
  useradd --create-home --shell /bin/bash --comment "Ridgeline Process Controls technician" -- "$VENDOR_EWS_USER"
fi
printf '%s:%s\n' "$VENDOR_EWS_USER" "$VENDOR_EWS_PASSWORD" | chpasswd

systemctl enable --now --no-block xrdp.service
