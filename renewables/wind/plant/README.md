# Wind Architecture

## Background

These phēnix networks were created as part of the DOE WETO-funded "Hardening
Wind Energy Systems from Cyber Threats" project. Some information on the project
is located
[here](https://www.energy.gov/sites/default/files/2021-10/fy21peerreview-gridintegration-snl-johnson2.pdf).

## Wind Turbine Plant Network

There are four topologies found in `phenix-configs`:
* `base` - baseline, unsecured wind site topology.
* `hids` - wind site with Host-based Intrusion Detection System (HIDS); Wazuh.
* `nids` - wind site with Network-based Intrusion Detection System (NIDS); Zeek.
* `soar` - wind site with NIDS, HIDS, and Security Orchestration, Automation,
  and Response (SOAR); there is a VM for SOAR but it is not running a SOAR
  solution. There are several commercial and open source solutions that can be
  deployed here.

`base` is the base topology, which is common across the remaining three
topologies. The four primary networks include the Wind Site Owner or Operator,
the Grid Operator, Original Equipment Manufacturer (OEM), and wind plant site.
Each of the networks illustrated on the top of Figure 1 have independent
internet protocol security (IPsec) virtual private network (VPN) connections to
the wind plant network. There are three protonuke VMs used for generating HTTP,
HTTPS, and SMTP traffic to represent Internet-based noise in the simulation.
Figure 1 includes 30 VMs representing wind turbine generators (WTGs), however,
the phenix topology only includes five VMs. One VM, `guard-cli`, is a Wireguard
client, which represents the laptop of an OEM technician (not represented on the
illustration, but is included in the topology).

Notes:

- It is possible to have any number of WTGs provided the hardware running
  minimega and phēnix have the resources to support them.
- We are currently using protonuke server on the WTGs and protonuke clients on
  the workstations generating HTTP traffic to simulate OT traffic (this is only
  temporary until our plans for generating representative OT traffic are
  realized).
- The PowerWorld save case was developed based on the [2000-bus synthetic grid
  model](https://electricgrids.engr.tamu.edu/electric-grid-test-cases/activsg2000/)
  developed by Texas A&M University, modified to include 30 wind turbine
  generator models connected to bus 2100 in the model.

![Figure 1: Baseline Environment](.images/wind-1.jpg)
<p align = "center">
Figure 1: Baseline Environment.
</p>

Figure 2 illustrates the `soar` topology.

![Figure 2: NIDS, HIDS, and SOAR Environment](.images/wind-5.jpg)
<p align = "center">
Figure 2: NIDS, HIDS, and SOAR Environment.
</p>

## Deploying in phēnix

The experiments described above have been designed to be deployed using
[phēnix](https://github.com/sandia-minimega/phenix). The easiest way to deploy
phēnix is to use the Docker Compose configs present in the `docker` directory of
the phēnix repository.

Since this repository relies on a custom phēnix user app, we recommend building
a custom Docker image for phēnix using the `Dockerfile` present in this
directory. This will use the latest phēnix Docker image and simply install the
custom phēnix user app (located in the `apps` directory) in it. Be sure to
update the `docker-compose.yml` file to use this custom image instead of the
default.

The phēnix topology configs in this repository include file injection references
to `/phenix/injects/wind-plant`. To ensure these injections work correctly,
users need to copy the contents of `phenix-injects` to
`/phenix/injects/wind-plant` on the server where phēnix is running (the "head
node").

```
mkdir -p /phenix/injects/wind-plant
cp -a phenix-injects/* /phenix/injects/wind-plant
```

The phēnix topology configs in this repository include VM disk references to
`vyos.qc2`, `kali.qc2`, `protonuke.qc2`, `wireguard.qc2`, `miniccc.qc2`,
`zeek.qc2`, `wazuh-manager.qc2`, `powerworld.qc2`, and `windows-7.qc2`. Recipes
for all of them except `powerworld.qc2` and `windows-7.qc2` are in the
[sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images)
repository. Its Makefile runs `phenix` in the phēnix Docker container and gives
it paths in the clone, so the clone must be under `/phenix`, which the container
sees at the same path. The Packer configuration in `phenix-images/workstation`
builds `windows-7.qc2`.

If you added the `wireguard` and `zeek` image configs from this repository's
former `phenix-images` directory, delete them first, because the Makefile stops
when an image config with the same name exists.

```
docker exec phenix phenix image delete wireguard
docker exec phenix phenix image delete zeek
```

To build the images, run the following commands on the server where the phēnix
Docker container is running. The `protonuke` recipe also needs a statically
linked `protonuke` binary (minimega's `cmd/protonuke`, built with
`CGO_ENABLED=0`) at the top of the clone, and the `vyos` recipe has its own
[instructions](https://github.com/sandialabs/sceptre-phenix-images/blob/main/scripts/vyos/README.md).

```
git clone https://github.com/sandialabs/sceptre-phenix-images.git /phenix/sceptre-phenix-images
cd /phenix/sceptre-phenix-images
make kali protonuke wireguard miniccc zeek wazuh-manager vyos
```

After each build, the Makefile injects minimega's `miniccc` agent into the
image. It takes the binary from `/phenix/miniccc`, or from `miniccc` at the top
of the clone. The `protonuke` app injects each Linux VM's `protonuke` arguments
into `/etc/default/protonuke`.

Then, move the image files to the `/phenix/images` directory so phenix and
minimega know where to grab them from.

To create an experiment, add a topology and scenario file from `phenix-configs`
to the phēnix store either via the command line or the UI. The `hids`, `nids`,
and `soar` scenarios take app settings from other scenarios (`fromScenario`), so
also add the `base` scenario (and, for `soar`, the `nids` scenario) to the store
before you create one of these experiments. Next, create an experiment in the
phēnix UI by referring to the appropriate topology and scenario for the given
network above.

Once you start the experiment, you can verify it is operating as expected by
checking the workstations on each network (e.g., `ws-ot`). They should all be
able to access the WTGs on the `192.168.100.0/24` network. You can also check
for `protonuke` logs on the WTGs to see that clients are connecting to them
(browse `/var/log/protonuke.log` via the VNC connection per VM).

- The username and password for any Linux VM is `root` with no password.
- The default username and password for a VyOS VM is `vyos` and `vyos`. However,
  you have an option of changing the password if you need to.
- The username and password for Kali VM is `root` and `toor` or `kali` and
  `kali` depending on the version of Kali that you create.
- The username and password for the Windows 7 image is `admin` and `password`.

## State of health

The `base`, `hids`, `nids` and `soar` scenarios end with the phēnix state of
health (soh) app. Its checks, by node group:

- Routers (`rtr-*`): every non-MGMT address; the flag file that the VyOS image
  writes after it runs the `vrouter` boot script; a route to every plant
  network, and on `rtr-site` also to the VPN subnet `10.222.222.0/24`. On
  `rtr-owop`, `rtr-grid`, `rtr-oem` and `rtr-site`: `strongswan.service`, ESP
  security associations in both directions with each IPsec peer, and each ACL
  ruleset of the scenario loaded in the firewall and applied to its interface.
- WTGs (`wtg-01` to `wtg-05`): address and gateway, `phenix.service`,
  `protonuke.service`, `/etc/default/protonuke`, port 80, and HTTP requests
  from all four workstations in `/var/log/protonuke.log`.
- Workstations (`ws-*`): address and gateway, the `protonuke` process, its
  startup script and log, and a TCP connection to port 80 of one WTG each
  (`ws-ot` to `wtg-04` and `wtg-05`). The connections from `ws-owop`, `ws-grid`
  and `ws-oem` go through the IPsec tunnels.
- Internet protonuke nodes: address and gateway, `phenix.service`,
  `protonuke.service` and `/etc/default/protonuke`; ports 80, 443 and 25 on
  `nuke-pns`; a TCP connection from `nuke-pnc1` and `nuke-pnc2` to port 80 of
  `nuke-pns` through `rtr-site`; HTTP, HTTPS and SMTP requests from both
  clients in the log of `nuke-pns`.
- WireGuard nodes: `wg0.conf` on both. On `guard-jh`: `wg-quick@wg0.service`
  and its startup script, UDP port 51820, `wg0` at `10.222.222.1/24` with IP
  forwarding on, and RDP (TCP 3389) to `ws-ot` through `rtr-site`.
- Kali nodes: `phenix.service` and the test harness files under
  `/root/wind-test-harness`; RDP from `kali-ot` to `ws-ot`.
- `wazuh-manager` (`hids`, `nids` and `soar`): the `wazuh-manager`,
  `elasticsearch` and `filebeat` services, ports 1514, 1515 and 9200, and
  manager alerts in the `wazuh-alerts-4.x-*` indices.
- `hids` and `soar` also check, on `ws-ot`, the Wazuh agent service `WazuhSvc`,
  its injected `ossec.conf` and a TCP connection to the manager's enrollment
  port 1515, and the injected `ossec.conf` on `guard-jh`.
- `nids` and `soar` also check, on `ids`, the injected Filebeat and Zeek configs
  and startup scripts, the `openvswitch-switch`, `zeek` and `filebeat`
  services, and packets on `gre_sys` from the mirror of the `ot` VLAN; on
  `kali-ot`, 30 seconds of pings to `rtr-site` on the `ot` VLAN, which also
  give the mirror traffic when no other OT host boots; and, on
  `wazuh-manager`, documents from the Filebeat of `ids` in `filebeat-*`.
- `soar` also checks, on `soar`, `phenix.service`, a TCP connection to
  Elasticsearch on the manager, and a ping to the tap `172.16.60.51` that the
  `windy` app creates on the phēnix host.

soh does not check `pwds`, whose image this repository does not describe, or
the Wazuh agent on `guard-jh`, which no topology starts. It skips the checks of
nodes that do not boot, except a `testCustomReachability` test whose source or
target does not boot: soh records that test as failed. The ICMP reachability
test (`testReachability`) is off, because the ACLs allow only the plant's own
flows.

To create and start an experiment with the checks, add the configs to the
store and create the experiment as described above. For example, for `hids`,
from this directory:

```
phenix config create phenix-configs/base/scenario.yml phenix-configs/hids/topology.yml phenix-configs/hids/scenario.yml
phenix experiment create wind-hids -t hids -s hids
phenix experiment start wind-hids
```

The checks run in the post-start stage after a `startupDelay` of 10 minutes,
and soh waits up to 15 more minutes (`c2Timeout`) for each guest's C2. Both are
sized for a loaded host such as a CI runner. The start returns when the checks
finish. A failed check is recorded, but it does not fail the start.
To run the checks again on the running experiment:

```
phenix experiment trigger running wind-hids soh
```

The results are in the SoH view of the phēnix web UI (the SoH button of the
experiment), under `status.apps.soh` in the output of
`phenix config get experiment/wind-hids`, and at
`GET /api/v1/experiments/wind-hids/soh` in the REST API.

The scenarios do not set `runPeriodically`, because the checks open HTTP, RDP
and Elasticsearch connections, which the NIDS and the HIDS would record during
a test harness run.

### Optional packet capture

soh can also capture traffic and show the flows in the Network Volume tab of
the SoH view. This adds an Elasticsearch and Kibana VM (4 vCPUs, 4096 MB) on
MGMT and one 512 MB Packetbeat VM for each captured host. It needs the
`elasticsearch.qc2` and `packetbeat.qc2` images (`make elasticsearch
packetbeat` in the images repository). To capture the protonuke traffic of
`nuke-pns` and `wtg-01`, paste this block under `metadata:` of the `soh` app
in a scenario:

```yaml
      packetCapture:
        elasticImage: elasticsearch.qc2
        packetBeatImage: packetbeat.qc2
        elasticServer:
          hostname: soh-elasticsearch
          ipAddress: 172.16.200.10/16
        captureHosts:
          nuke-pns:
          - IF0
          wtg-01:
          - IF0
```

Then stop and delete the experiment (`phenix experiment stop` and
`phenix experiment delete`), replace the stored scenario
(`phenix config delete scenario/<name>`, then `phenix config create` with the
file), and create the experiment again.
