#!/bin/bash
# phenix startup hook on ews: put the brewery's name, colors, and mark on xrdp's login
# screen (cm-brand-xrdp), then restart xrdp so it draws the next login with them. Nobody
# has a session this early in the boot.
/usr/local/bin/cm-brand-xrdp && systemctl try-restart xrdp.service
