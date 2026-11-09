#!/bin/bash
# phenix startup hook on a host with a Wazuh agent (cherrymountain app,
# DESIGN.md 14). Points the agent at the manager and enrolls it with the
# range enrollment password, which the agent reads from authd.pass, so the
# password never appears on a command line.
# Env (from /etc/default/cm-wazuh-agent): WAZUH_MANAGER, WAZUH_ENROLLMENT_PORT,
# WAZUH_AGENT_GROUP, WAZUH_REGISTRATION_PASSWORD.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/default/cm-wazuh-agent
: "${WAZUH_MANAGER:?set WAZUH_MANAGER}" "${WAZUH_REGISTRATION_PASSWORD:?set WAZUH_REGISTRATION_PASSWORD}"
ossec=/var/ossec
conf="${ossec}/etc/ossec.conf"

if [ ! -f "${conf}" ]; then
  echo "cm-wazuh-agent: no Wazuh agent installed; nothing to do"
  exit 0
fi

sed -i "s|<address>[^<]*</address>|<address>${WAZUH_MANAGER}</address>|" "${conf}"
sed -i "s|<port>1515</port>|<port>${WAZUH_ENROLLMENT_PORT:-1515}</port>|" "${conf}"
if [ -n "${WAZUH_AGENT_GROUP:-}" ] && ! grep -q '<groups>' "${conf}"; then
  sed -i "s|</enrollment>|  <groups>${WAZUH_AGENT_GROUP}</groups>\n    </enrollment>|" "${conf}"
fi
umask 077
printf '%s\n' "${WAZUH_REGISTRATION_PASSWORD}" > "${ossec}/etc/authd.pass"
chgrp wazuh "${ossec}/etc/authd.pass"
chmod 0640 "${ossec}/etc/authd.pass"

systemctl daemon-reload
systemctl enable --now --no-block wazuh-agent.service
echo "cm-wazuh-agent: enrollment configured"
