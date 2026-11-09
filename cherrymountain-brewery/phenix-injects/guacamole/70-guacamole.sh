#!/bin/bash
# phenix startup hook on dmz-jump (DESIGN.md 13). rsyslog restarts first, so its rule for
# the container logs is active before Docker starts the containers. The images are
# loaded into Docker and the stack starts through the units, which order themselves.
# --no-block: this script runs inside phenix.service during boot, and the first start
# takes minutes.
systemctl restart rsyslog.service
systemctl daemon-reload
systemctl enable --now --no-block cm-load-images.service cm-guacamole.service
