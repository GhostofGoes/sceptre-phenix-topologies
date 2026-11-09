#!/bin/bash
# phenix startup hook on inet-nuke. The topology injected the dnsmasq zone, the
# ntp app wrote chrony.conf (server mode) before boot, and the image starts
# protonuke. --no-block: this script runs inside phenix.service during boot.
systemctl enable --now --no-block dnsmasq.service
