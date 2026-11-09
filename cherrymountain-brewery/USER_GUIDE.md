# CherryMountain Brewery: user guide

This guide takes you from an empty host to a running CherryMountain range:
what to clone, what to build, how to start each variant, and how to check and
use it. [README.md](README.md) describes what is in the directory, and
[DESIGN.md](DESIGN.md) explains why it is built the way it is.

> **The range has not booted on a real phenix host yet.** The simulation, the
> apps, the PLC programs, and the network rules are tested offline, and the
> cellar slice runs in Docker. Every step below comes from the files in the
> repositories, but none has been run end to end on VMs, so expect to fix
> things on the first boot. Start with the ci variant.

## What you get

A notional Colorado microbrewery as an OT cyber range: a brewhouse, cellar,
packaging line, utilities, plant power, and building automation, with FUXA as
the SCADA and HMI, OpenPLC for the PLCs, ot-sim for the field devices, and a
HELICS co-simulation behind them. There are three variants:

| Variant | What it runs | VMs | vCPUs | RAM |
|---|---|---:|---:|---:|
| `ci` | The cellar slice: sim-ctl, sim-process, rio-cellar, plc-cellar, scada-fuxa | 5 | 7 | 8 GB |
| `lite` | The whole plant and its network, without the SOC and vendor access | 37 | 48 | 71 GB |
| `full` | Everything, with Malcolm, Wazuh, WireGuard, and Guacamole | 43 | 67 | 113 GB |

## What the host needs

- Ubuntu 24.04 on x86-64 with `/dev/kvm`. A 4-core, 16 GB machine is enough
  for ci. The full variant is sized for one host with 32 or more cores and
  128 GB: most VMs idle, so its 67 vCPUs are overcommitted, but its 113 GB of
  VM memory is not. Lite needs 71 GB for its VMs.
- About 150 GB of free disk under `/phenix` for all the images and the
  running VMs. The four ci images need much less.
- Docker with the Compose plugin, Git, and [Git LFS](https://git-lfs.com/).
- Go 1.24 or later, to build `miniccc` and `protonuke`.
- Python 3.12 with `pydantic` and `pyyaml`, for the check scripts. Node 22 is
  needed only if you rebuild a PLC program.
- Internet access while you build. The running range needs none.

## Get the code

The range needs a fork of phenix, because only its `phenix ui` keeps the
tunnels to the VMs' web pages open for an experiment started from the command
line. These are the repositories and branches the spike CI uses:

```shell
sudo apt install -y git-lfs
git lfs install

git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix.git
git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix-images.git
git clone https://github.com/sandia-minimega/minimega.git
git -C minimega checkout "$(cat sceptre-phenix/.github/e2e/minimega.ref)"

sudo mkdir -p /phenix && sudo chown "$USER" /phenix
git clone -b feat-secret-topo https://github.com/GhostofGoes/sceptre-phenix-topologies.git /phenix/topologies
cd /phenix/topologies && git lfs pull
```

The phenix build below also uses
`https://github.com/GhostofGoes/sceptre-phenix-apps` on branch
`ci-e2e-minimega`. Docker clones it for you, so you only need your own copy to
read it:

```shell
git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix-apps.git
```

Keep the topologies at `/phenix/topologies`. The `cherrymountain` app reads
`/phenix/topologies/cherrymountain-brewery` from inside the phenix container.

## Build and install

### phenix and minimega

Build the phenix image from the fork, with the fork's apps:

```shell
cd sceptre-phenix
docker build -f docker/Dockerfile \
  --build-arg APPS_REPO=github.com/GhostofGoes/sceptre-phenix-apps \
  --build-arg APPS_BRANCH=ci-e2e-minimega \
  -t phenix:cherrymountain .
```

Add the `cherrymountain` app and the `plantevent` SCORCH component on top:

```shell
docker build --build-arg PHENIX_IMAGE=phenix:cherrymountain \
  -t cherrymountain-phenix /phenix/topologies/cherrymountain-brewery/apps
```

Edit `docker/docker-compose.yml` in the phenix checkout so the `phenix`
service uses `image: cherrymountain-phenix`, then start phenix, minimega, and
the rest:

```shell
cd docker
docker compose up -d
docker exec phenix phenix experiment apps
```

The last command should list `cherrymountain`, `ot-sim`, `helics`, `mirror`,
`wireguard`, and `protonuke`. The phenix UI is on port 3000 of the host.

minimega runs from its published image. The VMs also need two minimega
programs inside them, which you build from the commit CI pins:

```shell
cd minimega
source scripts/env.bash
./scripts/version.bash
CGO_ENABLED=0 go install -ldflags="-s -w" ./cmd/miniccc ./cmd/protonuke
cp bin/miniccc bin/protonuke /phenix/
```

### The images

Each VM role has an image config in `phenix-images/`. Build them in this
order, so the layers you test first exist first: `cm-svc`, `cm-otsim`,
`cm-sim`, `cm-openplc`, `cm-fuxa`, `cm-historian`, `cm-desktop`, `cm-ews`,
`cm-guacamole`, `cm-wireguard`, `cm-wazuh`, and `cm-malcolm` (64 GB, the
slowest). The ci variant needs only `cm-sim`, `cm-otsim`, `cm-openplc`, and
`cm-fuxa`. Lite needs everything up to `cm-ews`.

Every build is the same three commands. The third one adds `miniccc`, which
phenix uses to reach into the VM:

```shell
for image in cm-sim cm-otsim cm-openplc cm-fuxa; do
  docker exec phenix phenix config create /phenix/topologies/cherrymountain-brewery/phenix-images/$image.yml
  docker exec phenix phenix image build -o /phenix/images -c -x $image
  docker exec phenix phenix image inject-miniexe /phenix/miniccc /phenix/images/$image.qc2
done
```

Lite and full need two more steps for protonuke, which stands in for internet
traffic:

```shell
cp /phenix/images/cm-svc.qc2 /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-desktop.qc2
```

They also need the VyOS router image, which comes from the images
repository. Its Makefile finds `/phenix/miniccc` by itself:

```shell
cd sceptre-phenix-images
sudo make vyos
sudo mv vyos.qc2 /phenix/images/
```

[phenix-images/README.md](phenix-images/README.md) says what is in each image.

### The PLC programs and FUXA projects

These are generated and committed, so you only check them. The PLC programs
are ZIP files in Git LFS:

```shell
cd /phenix/topologies/cherrymountain-brewery
ls -l phenix-injects/openplc/plc-cellar.zip      # about 100 KB, not 131 bytes
python3 sync.py --check
python3 fuxa/build_project.py --all --check
```

If you have Node 22, `plc/plc-cellar/build_zip.sh --check` and
`plc/common/build_zip.sh --all --check` also rebuild every ZIP and compare.
After a change to `plant/plant.yaml`, follow "Regenerate and test" in the
README.

## Start an experiment

`phenix workflow apply` does the whole job: it stores every config in
`phenix-configs/` (what `phenix config create` does by hand), copies
`phenix-injects/` to `/phenix/injects/<name>`, and creates and starts the
experiment. Use it rather than `phenix experiment create`, because the configs
find their injected files through a name that only the workflow fills in.

Start with ci. `-n` checks everything and changes nothing:

```shell
docker exec phenix phenix workflow apply -n -b cherrymountain-ci -c phenix-ci.yml /phenix/topologies/cherrymountain-brewery
docker exec phenix phenix workflow apply -b cherrymountain-ci -c phenix-ci.yml /phenix/topologies/cherrymountain-brewery
```

That creates the experiment `cherrymountain-ci`. When it works, move on to
lite, and then to full:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-lite -c phenix-lite.yml /phenix/topologies/cherrymountain-brewery
docker exec phenix phenix workflow apply /phenix/topologies/cherrymountain-brewery
```

The full variant's experiment is named `cherrymountain-brewery`. Add `-f` to
apply again over a running experiment. After the first apply, the usual
commands work too: `phenix experiment stop <name>` and
`phenix experiment start <name>`.

For full only: once it has started, set `options:df_default=false` on the
host's GRE port of the mirror (`ovs-vsctl set interface <gre port>
options:df_default=false`), or Malcolm loses full-size frames.

## Check that it works

Give it time. The PLCs compile their programs on first boot (3 to 8 minutes),
Wazuh takes 5 to 8, and Malcolm 10 to 15.

1. **VMs and state of health.** `docker exec phenix phenix vm info
   cherrymountain-ci` lists the VMs. The SoH tab of the experiment in the
   phenix UI shows the checks; they start 10 minutes after boot. A first pass
   that fails only on the PLCs or Malcolm is normal.
2. **The HMI.** FUXA is reached through a tunnel on a random host port. Look
   it up, every time:

   ```shell
   docker exec phenix phenix mm -n cherrymountain-ci cc tunnel list scada-fuxa
   ```

   Take the `src port` of the row whose `dst port` is 1881 and open
   `http://<host>:<port>`. Sign in as `admin` with `fuxa_password` from
   `/phenix/experiments/cherrymountain-ci/cherrymountain/secrets.json`. The
   Cellar view should show fermenter temperatures that move.
3. **The 3D view (lite and full).** `cm-viz` shows ground truth for the white
   cell. Look up sim-ctl's tunnel to port 8090 the same way and open
   `http://127.0.0.1:<port>/` in a browser on the phenix host (or through
   `ssh -L`); it refuses other host names. `/svg.html` is the same view
   without WebGL. Do not show it to trainees.
4. **Malcolm and Wazuh (full).** Look up the tunnels of `malcolm` and `wazuh`
   to port 443 and open `https://<host>:<port>`. The passwords are generated
   on first boot: on the VM's console (root, no password), read
   `/var/lib/cm-malcolm/admin-password` (user `malcolm`) and
   `/var/lib/cm-wazuh/passwords.txt` (user `admin`).
5. **A first SCORCH run (full).** Run 2 is the utility outage: it opens the
   main breaker, waits ten minutes, and checks that the generator carries the
   emergency panel.

   ```shell
   docker exec phenix phenix experiment scorch cherrymountain-brewery --run 2
   ```

   The runs are, in order from 0: `vendor-maintenance`, `baseline-brew-day`,
   `utility-outage`, `cellar-co2-leak`, and `packaging-shift`.

## Run an exercise

The white cell drives the plant through the event API on sim-ctl (lite and
full). SCORCH's `plantevent` component posts to it, and so can you. Look up
sim-ctl's tunnel to port 8080, then:

```shell
curl -s http://127.0.0.1:<port>/api/v1/health
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"event": "co2-release", "fv": "FV-102", "rate": "high", "duration": 600}' \
  http://127.0.0.1:<port>/api/v1/events
curl -s http://127.0.0.1:<port>/api/v1/kpis
```

The events are `start-batch`, `utility-breaker`, `genset-exercise`,
`chiller-fault`, `co2-release`, `co2-leak`, `gas-detection`,
`refrigerant-leak`, `bess-thermal`, `jam`, `cip-panel`, and `weather-step`.
The [plantevent README](apps/cherrymountain_apps/plantevent/README.md) lists
their parameters. Compare what happened (cm-viz, the ground-truth Grafana on
sim-ctl) with what the trainees saw (FUXA, Malcolm, Wazuh).

The simulation plane (MGMT), the VM consoles, and the phenix host are out of
bounds for players. Anyone who can reach the host can reach the tunnels, so
keep it on a trusted network.

**Reset between exercises.** Stop and start the experiment. The plant starts
again from its initial state, and the passwords stay the same because they
live in the experiment directory:

```shell
docker exec phenix phenix experiment stop cherrymountain-lite
docker exec phenix phenix experiment start cherrymountain-lite
```

**Clean up.** Stopping the experiment exports FUXA's alarm history and an
InfluxDB backup to the experiment's files. Delete the experiment when you are
done:

```shell
docker exec phenix phenix experiment stop cherrymountain-lite
docker exec phenix phenix experiment delete cherrymountain-lite
```

## Troubleshooting

- **"is a Git LFS pointer; run `git lfs pull`".** The PLC ZIPs were cloned as
  pointers. Run `git lfs pull` in `/phenix/topologies` and apply again.
- **No `/dev/kvm`.** minimega needs hardware virtualization. Check with
  `kvm-ok` (package `cpu-checker`); on a cloud VM, turn on nested
  virtualization.
- **An image build fails.** The builds download from Ubuntu, GitHub, PyPI,
  and vendor package repositories, so read the end of the build output for
  the URL that failed and try again. The `cm-sim` build installs the
  simulation from this repository at the commit named by `CM_REF` in
  `phenix-images/cm-sim.yml`, which must be pushed.
- **The VyOS build fails.** It builds VyOS's rolling branch from source, and
  that branch sometimes breaks for a day (a missing kernel package has
  stopped it before). Try again later, and keep a `vyos.qc2` that works.
- **Docker Hub rate limits.** `cm-guacamole` copies its container images from
  Docker Hub during the build. If it is refused, wait or build from a host
  that is signed in to Docker Hub.
- **The port you used yesterday is dead.** Tunnel ports are random and change
  whenever a VM restarts. Look them up again with `cc tunnel list`. If there
  are no tunnels at all, phenix is not the fork.
- **SoH fails on a PLC.** On the PLC's console, `journalctl -u
  cm-openplc-provision` shows the upload and the compile.
- **Nothing moves in FUXA.** Check the federation on sim-ctl: `/var/log`
  holds the HELICS broker's log, and `nft list table inet cm_mgmt` shows
  whether the MGMT policy is dropping packets.

## Where to go next

- [DESIGN.md §15.9](DESIGN.md#159-operations): the operations runbook,
  failure modes, and range security.
- [§10](DESIGN.md#10-network-architecture): zones, addresses, and the conduit
  matrix. [§14](DESIGN.md#14-blue-team-monitoring): detections.
- [§15.6](DESIGN.md#156-scorch-pipelines): the SCORCH runs.
  [§15.10](DESIGN.md#1510-white-cell-3d-view-cm-viz): the 3D view.
- [§19](DESIGN.md#19-risks-and-open-questions): what is known to be open.
- [spikes/README.md](spikes/README.md): the experiments behind the design
  decisions, such as [autotunnel](spikes/autotunnel/README.md) and
  [malcolm](spikes/malcolm/README.md).
