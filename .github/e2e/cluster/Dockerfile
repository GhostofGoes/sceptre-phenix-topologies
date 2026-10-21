# A minimega compute node for the cluster job: its own hostname, network
# namespace and Open vSwitch, running the minimega binary the job built on the
# runner (bind-mounted, so every node runs the same build). Ubuntu 24.04
# matches the runner's glibc, which that dynamically linked binary needs.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
# minimega's runtime dependencies, as the phenix minimega action installs them
# on the runner; qemu-system-modules-spice provides the QXL VGA device phenix
# asks for.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openvswitch-switch qemu-system-x86 qemu-kvm qemu-utils \
        qemu-system-modules-spice libpcap0.8t64 dnsmasq iproute2 \
        iputils-ping isc-dhcp-client ntfs-3g openssh-client kmod procps \
    && rm -rf /var/lib/apt/lists/*
COPY entry.sh /entry.sh
ENTRYPOINT ["/entry.sh"]
