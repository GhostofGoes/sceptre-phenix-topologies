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
