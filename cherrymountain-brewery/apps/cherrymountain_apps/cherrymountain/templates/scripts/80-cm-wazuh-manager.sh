#!/bin/bash
# phenix startup hook on the Wazuh manager (cherrymountain app, DESIGN.md 14).
# Requires the enrollment password from the agents (authd use_password), and lets
# an agent that comes back with no key take its name again at once.
# Env (from /etc/default/cm-wazuh-manager): WAZUH_REGISTRATION_PASSWORD.
set -euo pipefail

# CM_WAZUH_MANAGER_ENV and CM_OSSEC_DIR are for the tests.
# shellcheck source=/dev/null
. "${CM_WAZUH_MANAGER_ENV:-/etc/default/cm-wazuh-manager}"
: "${WAZUH_REGISTRATION_PASSWORD:?set WAZUH_REGISTRATION_PASSWORD}"
ossec="${CM_OSSEC_DIR:-/var/ossec}"
conf="${ossec}/etc/ossec.conf"

if [ ! -f "${conf}" ]; then
  echo "cm-wazuh-manager: no Wazuh manager installed; nothing to do"
  exit 0
fi

sed -i 's|<use_password>no</use_password>|<use_password>yes</use_password>|' "${conf}"
# Every start of a VM is a first boot, so an agent VM that is restarted alone comes
# back without its key and enrolls again under the same name. authd's defaults
# refuse that until the old agent has been disconnected for an hour and was
# registered an hour ago; the names here are the host names, so the new one always
# replaces the old. Written once, inside <auth>.
if ! grep -q '<force>' "${conf}"; then
  block="    <force>\n      <enabled>yes</enabled>\n      <key_mismatch>yes</key_mismatch>\n"
  block="${block}      <disconnected_time enabled=\"no\">1h</disconnected_time>\n"
  block="${block}      <after_registration_time>0</after_registration_time>\n    </force>"
  sed -i "0,\|</auth>|s|^\([[:space:]]*\)</auth>|${block}\n\1</auth>|" "${conf}"
fi
grep -q '<force>' "${conf}" || {
  echo "cm-wazuh-manager: FAIL: could not add the force block to ${conf}" >&2
  exit 1
}
umask 077
printf '%s\n' "${WAZUH_REGISTRATION_PASSWORD}" > "${ossec}/etc/authd.pass"
chgrp wazuh "${ossec}/etc/authd.pass"
chmod 0640 "${ossec}/etc/authd.pass"

systemctl try-restart --no-block wazuh-manager.service
echo "cm-wazuh-manager: password enrollment enabled"
