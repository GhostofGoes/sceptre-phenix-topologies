#!/bin/bash
# phenix startup hook on a host with a Wazuh agent (cherrymountain app,
# DESIGN.md 14). Points the agent at the manager and enrolls it with the
# range enrollment password, which the agent reads from authd.pass, so the
# password never appears on a command line.
# Env (from /etc/default/cm-wazuh-agent): WAZUH_MANAGER, WAZUH_ENROLLMENT_PORT,
# WAZUH_AGENT_GROUP, WAZUH_REGISTRATION_PASSWORD.
set -euo pipefail

# CM_WAZUH_AGENT_ENV and CM_OSSEC_DIR are for the tests.
# shellcheck source=/dev/null
. "${CM_WAZUH_AGENT_ENV:-/etc/default/cm-wazuh-agent}"
: "${WAZUH_MANAGER:?set WAZUH_MANAGER}" "${WAZUH_REGISTRATION_PASSWORD:?set WAZUH_REGISTRATION_PASSWORD}"
ossec="${CM_OSSEC_DIR:-/var/ossec}"
conf="${ossec}/etc/ossec.conf"

if [ ! -f "${conf}" ]; then
  echo "cm-wazuh-agent: no Wazuh agent installed; nothing to do"
  exit 0
fi

sed -i "s|<address>[^<]*</address>|<address>${WAZUH_MANAGER}</address>|" "${conf}"
# The packaged ossec.conf has no <enrollment> block (and a <groups>yes</groups> of
# its own, in the inventory settings), so the block is written here, inside
# <client>, once: it carries the enrollment port and the agent's group. Without it
# the agent enrolls into `default` and never gets its group's shared agent.conf.
if ! grep -q '<enrollment>' "${conf}"; then
  block="    <enrollment>\n      <enabled>yes</enabled>\n      <port>${WAZUH_ENROLLMENT_PORT:-1515}</port>\n"
  if [ -n "${WAZUH_AGENT_GROUP:-}" ]; then
    block="${block}      <groups>${WAZUH_AGENT_GROUP}</groups>\n"
  fi
  block="${block}    </enrollment>"
  sed -i "0,\|</client>|s|^\([[:space:]]*\)</client>|${block}\n\1</client>|" "${conf}"
fi
grep -q '<enrollment>' "${conf}" || {
  echo "cm-wazuh-agent: FAIL: could not add the enrollment block to ${conf}" >&2
  exit 1
}
umask 077
printf '%s\n' "${WAZUH_REGISTRATION_PASSWORD}" > "${ossec}/etc/authd.pass"
chgrp wazuh "${ossec}/etc/authd.pass"
chmod 0640 "${ossec}/etc/authd.pass"

systemctl daemon-reload
systemctl enable --now --no-block wazuh-agent.service
echo "cm-wazuh-agent: enrollment configured"
