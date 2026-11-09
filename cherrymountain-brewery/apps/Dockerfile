# phenix with the CherryMountain Brewery user app and SCORCH component
# (DESIGN.md 16): phenix-app-cherrymountain and
# phenix-scorch-component-plantevent.
#
# Build from this directory:
#   docker build -t cherrymountain-phenix cherrymountain-brewery/apps
#
# The base image already ships phenix-apps (AppBase, ComponentBase) and every
# Python dependency of this package, so it installs with --no-deps. The base is
# pinned by digest; to move it, resolve the digest of the phenix:main tag
# (or of the fork build DESIGN.md 16 calls for) and replace it here.
FROM ghcr.io/sandialabs/sceptre-phenix/phenix:main@sha256:b64b36764fa42a7ca199f42262a10644c07e967f63beaf1d34fa75648d0c2ef6

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY pyproject.toml README.md /opt/cherrymountain-apps/
COPY cherrymountain_apps /opt/cherrymountain-apps/cherrymountain_apps

# Check that phenix finds both executables on PATH (it runs
# phenix-app-<name> and phenix-scorch-component-<type>).
RUN python3 -m pip install --break-system-packages --no-cache-dir --no-deps /opt/cherrymountain-apps \
  && command -v phenix-app-cherrymountain phenix-scorch-component-plantevent \
  && python3 -c "import cherrymountain_apps.cherrymountain.app, cherrymountain_apps.plantevent.plantevent"
