#!/bin/bash
# phenix startup hook on a site's network sensor (docs/multisite/PLANS.md 4.6).
# Loads the container images into Docker, closes the sensor's two open sides
# (cm-sensor-acl), configures Malcolm's sensor profile on the first boot, and
# starts it. The units order themselves, so enabling them here is enough.
# --no-block: this script runs inside phenix.service during boot, and the first
# start takes minutes.
systemctl daemon-reload
systemctl enable --now --no-block cm-sensor-acl.service cm-load-images.service cm-sensor-provision.service cm-sensor.service
