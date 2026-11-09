#!/bin/bash
# phenix startup hook on the Wazuh manager (cherrymountain app, DESIGN.md 14).
# Requires the enrollment password from the agents (authd use_password).
# Env (from /etc/default/cm-wazuh-manager): WAZUH_REGISTRATION_PASSWORD.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/default/cm-wazuh-manager
: "${WAZUH_REGISTRATION_PASSWORD:?set WAZUH_REGISTRATION_PASSWORD}"
ossec=/var/ossec
conf="${ossec}/etc/ossec.conf"

if [ ! -f "${conf}" ]; then
  echo "cm-wazuh-manager: no Wazuh manager installed; nothing to do"
  exit 0
fi

sed -i 's|<use_password>no</use_password>|<use_password>yes</use_password>|' "${conf}"
umask 077
printf '%s\n' "${WAZUH_REGISTRATION_PASSWORD}" > "${ossec}/etc/authd.pass"
chgrp wazuh "${ossec}/etc/authd.pass"
chmod 0640 "${ossec}/etc/authd.pass"

systemctl try-restart --no-block wazuh-manager.service
echo "cm-wazuh-manager: password enrollment enabled"
