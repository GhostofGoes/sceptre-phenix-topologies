#!/bin/bash
# phenix startup hook on the EWS of a site whose jump host has accounts of the
# company's own staff (plant/sites.yaml, `staff`; docs/multisite/PLANS.md 4.6).
# Creates one local account for each person, from /etc/cherrymountain/staff.env
# (injected, mode 0600; range-only values): STAFF_COUNT, and STAFF<n>_USER and
# _EWS_PASSWORD for each person. Each person's own Guacamole connections on the
# site's jump host (ews-rdp-<account>, ews-ssh-<account>) carry that account, so
# a session opens straight into an Xfce desktop (xrdp) or a shell (sshd) as that
# person. sshd and xrdp-sesman log each session to auth.log with the name.
#
# The accounts are ordinary users in the group cm-engineers, which may read the
# PLCs' engineering credential in /etc/default/cm-ews (the app injects it
# root-only). These people program the PLCs, so they hold that credential
# anyway, and cm_plc_tool.py then logs the person and not root.
#
# Re-running is safe: an existing account only gets its password set again.
set -euo pipefail

SETTINGS=/etc/cherrymountain/staff.env
ENGINEERS=cm-engineers
PLC_CREDENTIAL=/etc/default/cm-ews
if [ ! -f "$SETTINGS" ]; then
  echo "71-ews-staff: no ${SETTINGS}; no staff account"
  exit 0
fi
# shellcheck source=/dev/null
. "$SETTINGS"

case "${STAFF_COUNT:-}" in
  [1-8]) ;;
  *)
    echo "71-ews-staff: STAFF_COUNT must be 1 to 8" >&2
    exit 1
    ;;
esac

groupadd --force --system -- "$ENGINEERS"
for n in $(seq 1 "$STAFF_COUNT"); do
  user_var="STAFF${n}_USER"
  password_var="STAFF${n}_EWS_PASSWORD"
  user="${!user_var:?set ${user_var}}"
  password="${!password_var:?set ${password_var}}"
  case "$user" in
    *[!A-Za-z0-9._-]* | -*)
      echo "71-ews-staff: account ${n} has a character that is not allowed in its name" >&2
      exit 1
      ;;
  esac
  if ! id -u -- "$user" > /dev/null 2>&1; then
    # -m copies /etc/skel, which holds the .xsession that starts Xfce for xrdp.
    useradd --create-home --shell /bin/bash --comment "CherryMountain Brewery staff ${user}" -- "$user"
  fi
  usermod --append --groups "$ENGINEERS" -- "$user"
  printf '%s:%s\n' "$user" "$password" | chpasswd
  echo "71-ews-staff: staff account ${user}"
done
if [ -f "$PLC_CREDENTIAL" ]; then
  chgrp -- "$ENGINEERS" "$PLC_CREDENTIAL"
  chmod 0640 "$PLC_CREDENTIAL"
fi

systemctl enable --now --no-block xrdp.service
