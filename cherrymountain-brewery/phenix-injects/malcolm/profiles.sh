# shellcheck shell=bash
# Malcolm sizing profiles (DESIGN.md 11), sourced by cm-malcolm-provision,
# cm-malcolm-start, cm-malcolm-wait-ready, and cm-malcolm-health.
#
#   full  8 vCPU / 24 GB: local OpenSearch (6 GB heap) and Logstash (3 GB heap),
#         Suricata, NetBox with PostgreSQL, and file scanning with Strelka. The
#         example heaps (OpenSearch 10 GB) leave too little of 24 GB for the rest.
#   slim  4 vCPU / 12 GB: the trimmed pipeline of the malcolm spike, which peaked
#         at 8.4 GiB with Suricata (OpenSearch 3 GB heap, Logstash 2 GB heap),
#         without Suricata, NetBox, and Strelka. nginx still needs `filescan` to
#         start (it is one of its fixed upstreams), so file scanning stays off
#         through PIPELINE_DISABLED instead.
#
# Neither figure is measured in a live-capture VM; the sizes are budgets until the
# first VM run (DESIGN.md 11).

# The profile for this VM: CM_MALCOLM_PROFILE is full, slim, or auto (full on a
# VM with 22 GB of memory or more, slim below that).
#
# The memory comes from getconf (the sysinfo system call), not from /proc/meminfo:
# cm-malcolm-provision.service runs with ProcSubset=pid, which leaves only the
# process directories in /proc. Reading /proc/meminfo there failed without ending
# the script (a command substitution does not inherit errexit), the comparison
# below failed on the empty value, and every VM was given the slim profile: no
# Suricata, no NetBox, no file scanning, on a 24 GB VM. A memory size that cannot
# be read is an error now, not a small VM.
cm_malcolm_profile() {
  local profile="${CM_MALCOLM_PROFILE:-auto}"
  local pages page_size mem_kb
  if [ "$profile" = auto ]; then
    pages=$(getconf _PHYS_PAGES 2> /dev/null) || pages=
    page_size=$(getconf PAGE_SIZE 2> /dev/null) || page_size=
    case "${pages}:${page_size}" in
      *[!0-9:]* | :* | *:)
        echo "cm_malcolm_profile: cannot read the memory size (getconf _PHYS_PAGES, PAGE_SIZE); set CM_MALCOLM_PROFILE to full or slim" >&2
        return 1
        ;;
    esac
    mem_kb=$((pages * (page_size / 1024)))
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

# Heap sizes (OpenSearch, Logstash) and whether Suricata and NetBox run.
cm_malcolm_heaps() {
  case "$1" in
    full) echo "6g 3g" ;;
    slim) echo "3g 2g" ;;
  esac
}

# The services of a profile, after opensearch (which starts first and must be
# healthy before the rest). The names are the compose services of Malcolm 26.10.0.
cm_malcolm_services() {
  local common="dashboards-helper dashboards logstash filebeat arkime arkime-live zeek zeek-live filescan pcap-monitor upload htadmin valkey valkey-cache api nginx-proxy"
  case "$1" in
    full) echo "${common} suricata suricata-live strelka-backend strelka-frontend strelka-manager freq netbox postgres" ;;
    slim) echo "${common}" ;;
  esac
}
