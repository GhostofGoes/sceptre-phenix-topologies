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
# One source for each log. The agent's package writes its ossec.conf at install
# time and, where journalctl exists, gives it a journald reader. noble's journald
# also forwards to rsyslog, which writes the same lines to /var/log/auth.log, syslog,
# and kern.log, the files the groups' shared agent.conf reads (DESIGN.md 14). With
# both, every sign-in, PLC-tool, and firewall-drop event reaches the manager twice,
# and a rule that counts (five failed logins) fires at half the count. The files
# stay, because the decoders and their samples are written for them.
if grep -q '<log_format>journald</log_format>' "${conf}"; then
  awk '
    /<localfile>/ { block = $0 ORS; inside = 1; next }
    inside {
      block = block $0 ORS
      if ($0 ~ /<\/localfile>/) {
        if (block !~ /<log_format>journald<\/log_format>/) printf "%s", block
        inside = 0
      }
      next
    }
    { print }
  ' "${conf}" > "${conf}.cm"
  cat "${conf}.cm" > "${conf}"
  rm -f "${conf}.cm"
fi
if grep -q '<log_format>journald</log_format>' "${conf}"; then
  echo "cm-wazuh-agent: FAIL: could not remove the journald reader from ${conf}" >&2
  exit 1
fi

umask 077
printf '%s\n' "${WAZUH_REGISTRATION_PASSWORD}" > "${ossec}/etc/authd.pass"
chgrp wazuh "${ossec}/etc/authd.pass"
chmod 0640 "${ossec}/etc/authd.pass"

systemctl daemon-reload
systemctl enable --now --no-block wazuh-agent.service
echo "cm-wazuh-agent: enrollment configured"
