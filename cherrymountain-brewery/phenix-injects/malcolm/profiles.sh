# shellcheck shell=bash
# Malcolm sizing profiles (DESIGN.md 11), sourced by cm-malcolm-provision,
# cm-malcolm-start, and cm-malcolm-health.
#
#   full  8 vCPU and 22 GB or more: every part of Malcolm that the range uses: local
#         OpenSearch and Logstash, live Zeek and Suricata, Arkime on rotated pcap,
#         NetBox with PostgreSQL, and file scanning with Strelka. The topology gives
#         the sensor 32 GB, the size Malcolm's documentation recommends; 24 GB is the
#         minimum it states, and the profile still runs there with a smaller
#         OpenSearch heap (see cm_malcolm_heaps).
#   slim  4 vCPU / 12 GB: the trimmed pipeline of the malcolm spike, which peaked
#         at 8.4 GiB with Suricata (OpenSearch 3 GB heap, Logstash 2 GB heap),
#         without Suricata, NetBox, and Strelka. nginx still needs `filescan` to
#         start (it is one of its fixed upstreams), so file scanning stays off
#         through PIPELINE_DISABLED instead. Malcolm does not support a host this
#         small; the profile is for a lab host that cannot give more.
#
# Neither figure is measured in a live-capture VM; the sizes are budgets until the
# first VM run (DESIGN.md 11).

# The memory of this VM in kB, from getconf (the sysinfo system call), not from
# /proc/meminfo: cm-malcolm-provision.service runs with ProcSubset=pid, which leaves
# only the process directories in /proc. Reading /proc/meminfo there failed without
# ending the script (a command substitution does not inherit errexit), the
# comparison failed on the empty value, and every VM was given the slim profile: no
# Suricata, no NetBox, no file scanning, on a 24 GB VM. A memory size that cannot
# be read is an error now, not a small VM.
cm_malcolm_mem_kb() {
  local pages page_size
  pages=$(getconf _PHYS_PAGES 2> /dev/null) || pages=
  page_size=$(getconf PAGE_SIZE 2> /dev/null) || page_size=
  case "${pages}:${page_size}" in
    *[!0-9:]* | :* | *:)
      echo "cm_malcolm_mem_kb: cannot read the memory size (getconf _PHYS_PAGES, PAGE_SIZE)" >&2
      return 1
      ;;
  esac
  printf '%s\n' "$((pages * (page_size / 1024)))"
}

# The profile for this VM: CM_MALCOLM_PROFILE is full, slim, or auto (full on a
# VM with 22 GB of memory or more, slim below that).
cm_malcolm_profile() {
  local profile="${CM_MALCOLM_PROFILE:-auto}"
  local mem_kb
  if [ "$profile" = auto ]; then
    mem_kb=$(cm_malcolm_mem_kb) || {
      echo "cm_malcolm_profile: set CM_MALCOLM_PROFILE to full or slim" >&2
      return 1
    }
    if [ "$mem_kb" -ge 22000000 ]; then
      profile=full
    else
      profile=slim
    fi
  fi
  case "$profile" in
    full | slim) printf '%s\n' "$profile" ;;
    *)
      echo "CM_MALCOLM_PROFILE must be auto, full, or slim, not ${profile}" >&2
      return 1
      ;;
  esac
}

# Heap sizes (OpenSearch, Logstash) of a profile on this VM. Malcolm's configurator
# would give OpenSearch half of the memory, a rule for a host that runs little else;
# the full profile also runs NetBox, Strelka, two Suricata and two Zeek containers,
# and Arkime, and the mirror carries a few Mbit/s. So: 10 GB (the heap of Malcolm's
# example configuration) on a VM of 30 GB or more, and 6 GB on a smaller one, where
# 10 GB would leave too little for the rest. Logstash gets 3 GB, the most the
# configurator gives it.
cm_malcolm_heaps() {
  local mem_kb
  case "$1" in
    full)
      mem_kb=$(cm_malcolm_mem_kb) || return 1
      if [ "$mem_kb" -ge 30000000 ]; then
        echo "10g 3g"
      else
        echo "6g 3g"
      fi
      ;;
    slim) echo "3g 2g" ;;
  esac
}

# The services of a profile, after opensearch (which starts first and must be
# healthy before the rest). The names are the compose services of Malcolm 26.10.0.
# pcap-capture writes the rotating pcap files that arkime indexes. Of Malcolm's 27
# services, keycloak (no single sign-on here) and arkime-live (Arkime's own live
# capture, for a sensor that forwards to another host) are never started.
cm_malcolm_services() {
  local common="dashboards-helper dashboards logstash filebeat arkime pcap-capture zeek zeek-live filescan pcap-monitor upload htadmin valkey valkey-cache api nginx-proxy"
  case "$1" in
    full) echo "${common} suricata suricata-live strelka-backend strelka-frontend strelka-manager freq netbox postgres" ;;
    slim) echo "${common}" ;;
  esac
}
