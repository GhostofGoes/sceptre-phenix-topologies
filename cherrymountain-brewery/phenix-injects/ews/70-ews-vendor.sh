#!/bin/bash
# phenix startup hook on the EWS (DESIGN.md 13). Creates one local account per
# Ridgeline technician (ridgeline-<name>), from /etc/cherrymountain/ridgeline.env
# (injected, mode 0600; range-only values): VENDOR_TECH_COUNT and VENDOR_TECH<n>_USER
# and _EWS_PASSWORD, or, in a file without a roster, the one VENDOR_EWS_USER. Each
# technician's own Guacamole connections on dmz-jump (ews-rdp-<account>,
# ews-ssh-<account>) carry that account, so a session opens straight into an Xfce
# desktop (xrdp) or a shell (sshd) as that person. The accounts are ordinary users:
# the vendor works through the engineering tools, and the EWS is where the audit trail
# of the vendor path is (sshd and xrdp-sesman log to auth.log, which the Wazuh agent
# reads, and cm-plc-tool logs the user). The shared account of earlier versions
# (ridgeline) is locked when it is not on the roster.
#
# Re-running is safe: an existing account only gets its password set again.
set -euo pipefail

SETTINGS=/etc/cherrymountain/ridgeline.env
LEGACY=ridgeline
if [ ! -f "$SETTINGS" ]; then
  echo "70-ews-vendor: no ${SETTINGS}; no vendor account"
  exit 0
fi
# shellcheck source=/dev/null
. "$SETTINGS"

users=()
passwords=()
if [ -n "${VENDOR_TECH_COUNT:-}" ]; then
  case "$VENDOR_TECH_COUNT" in
    [1-8]) ;;
    *)
      echo "70-ews-vendor: VENDOR_TECH_COUNT must be 1 to 8" >&2
      exit 1
      ;;
  esac
  for n in $(seq 1 "$VENDOR_TECH_COUNT"); do
    user_var="VENDOR_TECH${n}_USER"
    password_var="VENDOR_TECH${n}_EWS_PASSWORD"
    users+=("${!user_var:?set ${user_var}}")
    passwords+=("${!password_var:?set ${password_var}}")
  done
else
  : "${VENDOR_EWS_USER:?set VENDOR_EWS_USER}" "${VENDOR_EWS_PASSWORD:?set VENDOR_EWS_PASSWORD}"
  users+=("$VENDOR_EWS_USER")
  passwords+=("$VENDOR_EWS_PASSWORD")
fi

for i in "${!users[@]}"; do
  user="${users[$i]}"
  case "$user" in
    *[!A-Za-z0-9._-]* | '' | -*)
      echo "70-ews-vendor: technician $((i + 1)) has a character that is not allowed in its account name" >&2
      exit 1
      ;;
  esac
  if ! id -u -- "$user" > /dev/null 2>&1; then
    # -m copies /etc/skel, which holds the .xsession that starts Xfce for xrdp.
    useradd --create-home --shell /bin/bash --comment "Ridgeline Process Controls technician ${user}" -- "$user"
  fi
  printf '%s:%s\n' "$user" "${passwords[$i]}" | chpasswd
  echo "70-ews-vendor: technician account ${user}"
done

on_roster=false
for user in "${users[@]}"; do
  [ "$user" = "$LEGACY" ] && on_roster=true
done
if [ "$on_roster" = false ] && id -u -- "$LEGACY" > /dev/null 2>&1; then
  usermod --lock --expiredate 1 -- "$LEGACY"
  echo "70-ews-vendor: locked the shared account ${LEGACY}"
fi

systemctl enable --now --no-block xrdp.service
