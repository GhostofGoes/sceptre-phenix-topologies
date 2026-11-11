#!/bin/bash
# phenix startup hook on malcolm (DESIGN.md 14). Loads the container images into
# Docker, configures Malcolm on the first boot, and starts it. The units order
# themselves (images, then configuration, then start), so enabling them here is
# enough. --no-block: this script runs inside phenix.service during boot, and the
# first start takes minutes.
systemctl daemon-reload
systemctl enable --now --no-block cm-load-images.service cm-malcolm-provision.service cm-malcolm.service
