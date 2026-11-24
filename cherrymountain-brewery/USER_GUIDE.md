# CherryMountain Brewery: user guide

This guide starts with an empty host and ends with a CherryMountain range that
runs. Then it shows how to connect to each part of the range and how to use it.
The first time, follow the guide from top to bottom.
[README.md](README.md) lists what is in this directory.
[DESIGN.md](docs/DESIGN.md) explains why the range has this design.

> Start with the small `ci` variant. Expect to fix problems on the first boot.
> The range did not boot on a real phenix host yet. The tests of the
> simulation, the apps, the PLC programs, and the network rules ran offline.
> Parts of the range ran in Docker and on loopback. Each step below comes from
> the files and from the source code of phenix, minimega, and the vendors. No
> person ran the steps from start to end on VMs.
> [Known limits](#known-limits) lists what to expect.

## The path in one look

| Step | What you do | Approximate time |
|---|---|---|
| [1](#1-pick-a-variant) | Pick a variant that fits your host | |
| [2](#2-prepare-the-host) | Install Docker, Git LFS, and Go. Make sure that KVM is available | 15 min |
| [3](#3-get-the-code) | Clone four repositories | 5 min |
| [4](#4-build-and-start-phenix) | Build the phenix container and start it | 30 min |
| [5](#5-build-the-vm-images) | Build the VM images (4 for `ci`, 14 for `full`) | 1 h to several hours |
| [6](#6-start-the-range) | Apply the workflow. It creates and starts the experiment | 5 min |
| [7](#7-wait-then-check-health) | Wait for the first boot. Then make sure that the range is healthy | 15 to 30 min |
| [8](#8-connect-from-your-own-computer) | Run `phenix-tunneler` on your computer and open the web pages | 5 min |
| [9](#9-passwords) | Find the passwords | |
| [10](#10-a-tour-use-each-part) | Use each part: SCADA, PLCs, vendor access, SOC, white cell | |

## 1. Pick a variant

The range is a notional microbrewery in Colorado. It has a brewhouse, a cellar,
a packaging line, utilities, plant power, and building automation. FUXA is the
SCADA (the supervisory control software) and the HMI (the operator screens).
OpenPLC runs the PLCs (the controllers), and ot-sim runs the field devices. A
HELICS simulation supplies the physics behind them.

A variant is one size of the range. The SOC (security operations center) is the
part that monitors the plant network. A blue team is the group of defenders in
an exercise.

| Variant | What it runs | VMs | vCPUs | RAM | Use it for |
|---|---|---:|---:|---:|---|
| `ci` | The cellar only: one simulation node pair, one remote I/O, one PLC, one FUXA | 5 | 7 | 8 GB | A first boot, and a laptop |
| `lite` | The whole plant and its network, without the SOC and the vendor path | 37 | 45 | 69 GB | Process and network work |
| `full` | All parts, with Malcolm, Wazuh, a Kali Purple workstation, WireGuard, and Guacamole | 44 | 68 | 160 GB | Exercises with a blue team |

Run one variant at a time. The variants use the same VLAN numbers, and minimega
refuses the second one. An experiment is one range that phenix manages. A
stopped experiment of a different variant can stay.

A fourth variant, `fleet`, is planned and not built. It is four breweries and a
head office on a cluster of three nodes. A VPN over a mock internet joins them.
[The multi-site variant](#the-multi-site-variant-fleet) shows how to run the
parts that exist.

## 2. Prepare the host

You need one machine with these properties:

- Ubuntu 24.04 on x86-64 with `/dev/kvm`. To make sure that KVM is available,
  run `kvm-ok` (package `cpu-checker`). On a cloud VM, turn on nested
  virtualization.
- Sufficient CPU and memory for the variant. The list after this one gives the
  numbers.
- About 150 GB of free disk space under `/phenix` for all images and the VMs
  that run. The four `ci` images need about 20 GB.
- Docker with the Compose plugin, Git, [Git LFS](https://git-lfs.com/), and
  Go 1.24 or later.
- Internet access while you build. The range does not need internet access
  while it runs.

The CPU and memory for each variant are as follows:

- `ci` runs on 4 cores and 16 GB.
- `lite` needs about 80 GB.
- `full` fits a host with 32 or more cores and 512 GB. Most VMs idle, so
  `full` overcommits its 68 vCPUs. It does not overcommit its 160 GB of VM
  memory. The remaining memory is for the host.

On a smaller host, lower the memory of the two large nodes in
`phenix-configs/topology-soc.yml`. Make the same change in
`tools/netcheck/netcheck/plan.py`, because a test compares the two files.
Change the `memory` of malcolm from 49152 to 32768, or to 24576. Malcolm
supports no less than 24576, and the sensor then runs a smaller OpenSearch
heap. Change the `memory` of soc-purple from 24576 to 16384, or delete
soc-purple.

Run these commands on the host:

```shell
sudo apt update
sudo apt install -y git git-lfs cpu-checker golang-go
git lfs install
kvm-ok                      # must say "KVM acceleration can be used"
docker compose version      # install Docker Engine first if this fails
go version                  # 1.24 or later; if older, install Go from go.dev
```

## 3. Get the code

The range needs a fork of phenix, on the branch `ci-e2e-minimega`. Only that
fork keeps the port forwards to the web pages of the VMs open when you start an
experiment from the command line.

The clone of the images repository uses the branch
`feat-cherrymountain-images` of the fork. That branch has upstream `main`, the
fixes of the fork, and the Kali Purple recipe. It also has a copy of the image
configurations of the range in `configs/cherrymountain/`.

Run these commands on the host:

```shell
git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix.git
git clone -b feat-cherrymountain-images https://github.com/GhostofGoes/sceptre-phenix-images.git
git clone https://github.com/sandia-minimega/minimega.git
git -C minimega checkout "$(cat sceptre-phenix/.github/e2e/minimega.ref)"

sudo mkdir -p /phenix && sudo chown "$USER" /phenix
git clone -b feat-secret-topo https://github.com/GhostofGoes/sceptre-phenix-topologies.git /phenix/topologies
git -C /phenix/topologies lfs pull
```

Keep the topologies at `/phenix/topologies`. The `cherrymountain` app reads
`/phenix/topologies/cherrymountain-brewery` from inside the phenix container.

Make sure that Git LFS fetched the PLC programs. A file of about 130 bytes is
only a pointer to the real file:

```shell
ls -l /phenix/topologies/cherrymountain-brewery/phenix-injects/openplc/plc-cellar.zip   # about 100 KB
```

## 4. Build and start phenix

Build the phenix image from the fork of phenix, with the apps of the apps fork.
The build takes about 20 minutes. It needs BuildKit, which current Docker uses
by default. The branch `feat-cherrymountain-apps` of the apps fork holds the
`cherrymountain` app of the range and its SCORCH component, `plantevent`. As a
result, the image needs no second build. Run these commands on the host, in the
directory that holds the clones:

```shell
cd sceptre-phenix
docker build -f docker/Dockerfile \
  --build-arg APPS_REPO=github.com/GhostofGoes/sceptre-phenix-apps \
  --build-arg APPS_BRANCH=feat-cherrymountain-apps \
  -t phenix:cherrymountain .
```

Tell Compose to use that image. Then start phenix and minimega. With the
override file, you do not have to edit the `docker-compose.yml` of the fork:

```shell
cd docker
cat > docker-compose.override.yml <<'EOF'
services:
  phenix:
    image: phenix:cherrymountain
EOF
docker compose up -d
docker exec phenix phenix experiment apps
```

The last command must list `cherrymountain`, `ot-sim`, `helics`, `mirror`,
`wireguard`, and `protonuke`. The phenix web page is now at
`http://<host>:3000`. Keep the host on a network that you trust. The page has
no login in this setup.

Build the two programs that go into each VM. phenix uses `miniccc` to run
commands in a VM. `protonuke` makes traffic that stands in for the internet:

```shell
cd ../../minimega
source scripts/env.bash
./scripts/version.bash
CGO_ENABLED=0 go install -ldflags="-s -w" ./cmd/miniccc ./cmd/protonuke
cp bin/miniccc bin/protonuke /phenix/
```

## 5. Build the VM images

Each type of VM has an image configuration in `phenix-images/`. Each image
needs the same three commands. The build runs in the phenix container. It needs
internet access and does not need KVM. Define this function in a shell on the
host:

```shell
build_image() {
  docker exec phenix phenix config create "/phenix/topologies/cherrymountain-brewery/phenix-images/$1.yml"
  docker exec phenix phenix image build -o /phenix/images -c -x "$1"
  docker exec phenix phenix image inject-miniexe /phenix/miniccc "/phenix/images/$1.qc2"
}
```

| Variant | Images to build, in this order |
|---|---|
| `ci` | `cm-sim` `cm-otsim` `cm-openplc` `cm-fuxa` |
| `lite` | `cm-svc` `cm-otsim` `cm-sim` `cm-openplc` `cm-fuxa` `cm-historian` `cm-desktop` `cm-ews`, then VyOS and the two protonuke steps below |
| `full` | All images for `lite`, then `cm-guacamole` `cm-wireguard` `cm-wazuh` `cm-malcolm`, then Kali Purple below |

For `ci`, run this command in the same shell:

```shell
for image in cm-sim cm-otsim cm-openplc cm-fuxa; do build_image "$image"; done    # ci
```

`lite` and `full` need three more steps. Run them on the host, in the directory
that holds the clones:

```shell
# protonuke, the stand-in for internet traffic
cp /phenix/images/cm-svc.qc2 /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-desktop.qc2

# the VyOS router image, from the images repository (it finds /phenix/miniccc by itself)
cd sceptre-phenix-images && sudo make vyos && sudo mv vyos.qc2 /phenix/images/
```

`full` also needs the Kali Purple image for soc-purple. That image is not one
of the configurations of this range. Its recipe is in
`sandialabs/sceptre-phenix-images` since 2026-11-15, and the branch from step 3
has `scripts/kali-purple.sh`. The first command below stages about 2 GB of
container images. It needs Docker and internet access. With those container
images in the VM image, parseus installs at first boot without a network. The
range has no connection to the internet.

```shell
cd sceptre-phenix-images
make kali-purple-bundle
make kali-purple PARSEUS_OFFLINE=1 && mv kali-purple.qc2 /phenix/images/
```

The CI of the images fork builds each image of the range from the copies in
`configs/cherrymountain/`, with `make cm-sim` and the equivalent targets. No
published build exists yet, so you must build the images on your host.

Facts about image builds:

- `cm-malcolm` (64 GB) is the slowest build by a large margin. `cm-desktop` and
  `cm-ews` install a desktop and also take a long time.
- A build leaves `<name>.tar` (a cache of the base system) and `<name>.log`
  next to the image. If a build fails, read the log. To save disk space, delete
  the `.tar`. Also delete it after you change the package list of an image.
- Before you build an image again, run
  `docker exec phenix phenix image delete <name>`. `phenix config create` fails
  when the configuration already exists.
- `cm-sim` installs the simulation from this repository. It uses the commit
  that `CM_REF` names in `phenix-images/cm-sim.yml`, so that commit must be on
  GitHub.
- [phenix-images/README.md](phenix-images/README.md) lists the contents of each
  image.

## 6. Start the range

One command does all the work. `phenix workflow apply` stores each
configuration file from `phenix-configs/` in phenix. It copies
`phenix-injects/` to `/phenix/injects/<name>`. Then it creates and starts the
experiment. Run the command from outside the directory.

Start with `ci`. With `-n`, the first command only examines the files and
changes nothing. Run these commands on the phenix host:

```shell
cd /phenix
T=/phenix/topologies/cherrymountain-brewery
docker exec phenix phenix workflow apply -n -b cherrymountain-ci -c phenix-ci.yml $T
docker exec phenix phenix workflow apply    -b cherrymountain-ci -c phenix-ci.yml $T
```

When `ci` works, stop it. Then start a larger variant:

```shell
docker exec phenix phenix experiment stop cherrymountain-ci
docker exec phenix phenix workflow apply -b cherrymountain-lite -c phenix-lite.yml $T    # lite
docker exec phenix phenix workflow apply $T                                              # full
```

| Variant | Experiment name |
|---|---|
| `ci` | `cherrymountain-ci` |
| `lite` | `cherrymountain-lite` |
| `full` | `cherrymountain-brewery` |

- Use `workflow apply`, not `phenix experiment create`. The configurations find
  their injected files through a name that only the workflow fills in.
- To apply again to an experiment that runs, add `-f`. If you apply to a
  stopped experiment, phenix updates it and starts it.
- A missing image does not stop the start. phenix marks that VM "do not boot"
  and prints a warning. Read the output.

In `full`, the packet mirror of Malcolm needs one option on its tunnel port
(`df_default=false`). Without the option, the mirror loses full-size packets.
The app of the range sets the option after each start. Step 7 has a test for
it.

## 7. Wait, then check health

The VMs start in layers, and each layer waits for the layer before it. The
order is: routers, the simulation, field devices, PLCs, then FUXA. Each start
is a first boot. phenix builds the disk of each VM again from its image, so
allow time for it.

| What | Time after its VM starts |
|---|---|
| Routers (VyOS) | 2 to 3 min |
| Simulation and field devices | 2 to 4 min |
| PLCs (each compiles its program) | 3 to 8 min |
| FUXA | 2 min after its PLCs |
| Wazuh (`full`) | 5 to 10 min |
| Malcolm (`full`) | 10 to 15 min |

Then do these steps in this order:

1. Make sure that the VMs run. Run this command on the phenix host:

   ```shell
   docker exec phenix phenix vm info cherrymountain-ci
   ```

   As an alternative, open `http://<host>:3000/experiment/cherrymountain-ci`.
   Each VM is a tile. A VM whose tile shows a live screenshot booted.

2. Open the state of health (SoH) page at
   `http://<host>:3000/soh/cherrymountain-ci`. The SoH checks start about 10
   minutes after the last VM starts, so the page stays empty for some time. A
   first pass that fails only on the PLCs or on Malcolm is normal, because they
   were not ready. To run the checks again, use this command on the phenix
   host:

   ```shell
   docker exec phenix phenix experiment trigger running cherrymountain-ci soh
   ```

3. Make sure that the simulation runs (`lite` and `full`). Start the tunneler
   of [step 8](#8-connect-from-your-own-computer) first. Then run this command
   on your computer:

   ```shell
   curl -s http://localhost:18080/api/v1/health
   ```

   `"status": "ok"` with a `sim_time` that increases means that each part of
   the simulation joined. `"starting"` means that one part did not join. See
   [Troubleshooting](#troubleshooting).

4. Read what the app of the range saw. Every five minutes, the app reads the
   provisioning state of each VM and the key figures of the plant. Run this
   command on the phenix host:

   ```shell
   docker exec phenix phenix config get experiment/cherrymountain-ci -o json -p | grep -A40 '"cherrymountain"'
   ```

   Look for `"provisioned": "n/m"`. For each host, look for `provisioned`,
   `pending`, `failed`, or `unreachable`.

5. Make sure that the mirror option is set (`full`). This command on the
   phenix host must print `"dfDefault": "set"`:

   ```shell
   docker exec phenix phenix config get experiment/cherrymountain-brewery -o json -p | grep dfDefault
   ```

If one VM does not start its control connection, each VM that waits for it
stays down. SoH then does not start, because phenix has no timeout there. Open
the console of that VM (step 8). Then find the cause.

## 8. Connect from your own computer

The VMs are on closed networks. phenix forwards a list of their web pages for
you. A small program on your computer, `phenix-tunneler`, puts each page on a
fixed local port. Your computer only needs to reach the phenix web page.

1. Download the program. Open `http://<host>:3000`. Click the Tunneler tab.
   Download the file for your system. As an alternative, fetch it directly from
   `http://<host>:3000/downloads/tunneler/phenix-tunneler-linux-amd64`. The
   other file names end in `-darwin-arm64`, `-darwin-amd64`, and
   `-windows-amd64.exe`.
2. Run the program on your computer. Do not stop it. Do not put a slash at the
   end of the address:

   ```shell
   chmod +x phenix-tunneler-linux-amd64
   ./phenix-tunneler-linux-amd64 serve http://<host>:3000
   ```

   The program prints `activated local listener on port ...` for each forward.
3. Open the pages in your browser:

| Open this on your computer | What it is | Variant | Sign in |
|---|---|---|---|
| `http://localhost:11881` | scada-fuxa: the process SCADA and HMI | all | None to look. `operator` to operate. `admin` to edit |
| `http://localhost:11882` | bas-fuxa: the front end of the building automation | lite, full | The same as scada-fuxa |
| `http://localhost:13000` | historian: Grafana with process trends | lite, full | None to look. The account is `admin` |
| `http://localhost:18090` | cm-viz: the 3D view of the real state of the plant | lite, full | None |
| `http://localhost:18080/api/v1/health` | event API: the interface that drives the plant | lite, full | None |
| `http://localhost:13001` | ground-truth Grafana on sim-ctl | lite, full | None to look. The account is `admin` |
| `http://localhost:19200` | ground-truth OpenSearch | lite, full | None |
| `https://localhost:18443` | Malcolm: network security monitoring | full | `malcolm` |
| `https://localhost:18444` | Wazuh: the SIEM (security event manager) | full | `admin` |

The screens of the brewery show its mark, a phoenix over a summit, and its dark
and ember colors. These screens are FUXA, the Guacamole sign-in, the Grafana
logo, and the wallpaper of the desktops. Malcolm and Wazuh keep their own look.

Passwords are in [step 9](#9-passwords). Malcolm and Wazuh use self-signed
certificates, so your browser shows a warning one time.

More facts about the connections:

- The ports stay the same when a VM restarts. In a second terminal,
  `phenix-tunneler list` shows them. `phenix-tunneler move <id> <port>` moves a
  port that conflicts with a program on your computer.
- If you turned on the phenix login, add `--username <you>` to `serve`. Then
  run `phenix-tunneler activate <id>` one time for each forward in `list`.
- To reach a different port of a VM, open the tile of the VM in the phenix
  page. Click the arrow ("create port forward"). Select a local port and the
  port of the VM. The tunneler uses the new forward immediately. Use this
  method for a real RDP client on the engineering workstation (EWS). The VM of
  the EWS is `ews`, and the port is 3389.
- To use the screen and keyboard of a VM, click the screenshot of the VM in the
  phenix page. Each Linux VM built from the images of the range logs in as
  `root` with no password on its console. Use the console to reach the
  desktops, the HMI kiosks, and a shell on a VM.

Two VMs are different. On `ews`, the password of root is locked. Its desktop
still logs in as `root` automatically, and a text console does not. The
soc-purple VM is the Kali Purple image from upstream, and the range does not
set its console login.

Without the tunneler, on the phenix host, each forward also listens on a random
host port. Find the port each time. Use the row whose `dst port` is the port of
the VM (for FUXA, 1881):

```shell
docker exec phenix phenix mm -n cherrymountain-ci cc tunnel list scada-fuxa    # then open http://<host>:<src port>
```

cm-viz answers only to the names `localhost` and `127.0.0.1`. As a result, it
works through the tunneler or `ssh -L`, and not through the address of the
host.

## 9. Passwords

The app of the range makes new passwords for each experiment. It keeps them in
one file on the phenix host, with mode 0600. Run this command on the phenix
host:

```shell
sudo cat /phenix/experiments/<experiment>/cherrymountain/secrets.json
```

| For | User | Key in `secrets.json` |
|---|---|---|
| FUXA, to operate from your own browser (scada-fuxa, bas-fuxa). The operator stations sign in with it automatically | `operator` | `fuxa_operator_password` |
| FUXA editor (scada-fuxa, bas-fuxa) | `admin` | `fuxa_password` |
| OpenPLC runtime on each PLC (`https://<plc>:8443`) | `admin` | `openplc_password` |
| Grafana, on the historian and on sim-ctl | `admin` | `grafana_password` |
| InfluxDB on the historian | `admin` | `influx_password` (API token: `influx_token`) |
| Guacamole administrator | `cmadmin` | `guacamole_password` |
| Vendor technician, Guacamole | `ridgeline-akim` | `vendor_guac_password`, and a one-time code from `vendor_totp_secret` |
| Vendor technician, on the EWS | `ridgeline-akim` | `vendor_ews_password` |
| Malcolm | `malcolm` | `malcolm_password` |
| Wazuh dashboard | `admin` | `wazuh_password` |

The passwords stay the same after a stop and start, and after a new
`workflow apply`. If you delete the experiment, phenix deletes them. A second
technician, `ridgeline-jdiaz`, is in `vendor.json` next to `secrets.json`.

If a sign-in to Malcolm or Wazuh fails, read the password that the VM set. On
the malcolm console, run `cat /var/lib/cm-malcolm/admin-password`. On the wazuh
console, run `grep 'user admin' /var/lib/cm-wazuh/passwords.txt`.

## 10. A tour: use each part

### Operate the plant (scada-fuxa)

Open `http://localhost:11881`. The first screen is the Overview. It shows the
whole plant on one screen, and product moves from left to right. Click a panel
to open its area: Brewhouse, Cellar, Packaging, Utilities, Power, or
Cellar CO2.

The menu on the left has the same screens and the Batch view. It also has a
points table for each controller (Cellar points, for example) with each tag of
the controller. `ci` has only the Cellar screen. To open a view directly, use
`http://localhost:11881/home?viewName=Cellar`.

How to read a screen:

- Gray is normal. A screen with no faults has no red and no yellow on it.
- A color shows the contents of a vessel or a pipe. Wort is brown, beer is
  gold, water is blue, and hot liquor is orange. Glycol is teal, steam is pale
  gray, CO₂ is green, and cleaning solution is purple. A fermenter changes from
  brown to gold as its extract (sugar content) decreases.
- Blue means on. A pump or a fan that runs is blue. An open valve is blue, and
  so is a lamp whose text is true. A closed breaker is a dark square, and the
  wire behind it is dark. A fan that runs turns. A pipe with flow shows the
  flow in motion.
- A flag is an alarm. It appears next to the value that it belongs to. A red
  square with a 1 is critical. A triangle with a 2 is high. A yellow triangle
  that points down, with a 3, is medium. A blue diamond with a 4 is low. Hold
  the pointer on a flag to read the text of the alarm. The list in the header
  has all alarms.
- A scale shows where a value is. The shaded band is the normal range. A line
  across the band is an alarm limit. The black pointer is the value. The hollow
  triangle on the left is the setpoint.
- A CO₂ sensor has a bar with marks at the three tiers (5,000, 15,000, and
  30,000 ppm). Each third fills as the value goes through it. As a result, a
  bar that reaches the first mark is at tier 1.
- On the Cellar CO2 screen, each tier is a lamp. Under each lamp, the screen
  shows the action of the tier relay and whether the action occurred. The
  actions are the purge fans, the carbonation inhibit, the horns, and the
  supply shutoff. A lit tier above an effect that is not lit means that the
  safety action did not occur.
- On the plan of the cellar, a green ring around a fermenter means that its
  spunding valve (a pressure relief valve) vents.
- A white box is a value that you can change. A button is a command. You can
  only read an item that has no box and no button.
- A trend shows the last hour.

What you can do on a screen:

- Look. The fermenter temperatures on the Cellar screen must change in less
  than one minute. If the values do not change, the simulation does not reach
  the PLCs. See [Troubleshooting](#troubleshooting).
- Change a value. Without a login, FUXA only shows values. Click the account
  icon at the top right, or try to write a value. FUXA then opens its sign-in
  box. Sign in as `operator`. The sign-in lasts 12 hours.
- Use the boxes and buttons. The screens have a box or a button for the values
  that an operator changes most. A points table has a box next to each value
  that you can write. Type a value. Then press Enter. Type engineering values
  (`66.0` for 66 °F). Bits take `1` or `0`.
- Edit the screens. Only `admin` can open the editor (`/editor`).
- Acknowledge an alarm. Open the alarm list in the header. Each alarm above
  the lowest level stays until a person acknowledges it.

| To do this | Do this | On view |
|---|---|---|
| Change the temperature of a fermenter | Press "By hand" under that fermenter. Then type in its "side" and "cone" boxes (`CEL.FV101.TT01.SP` and `TT02.SP`). "Profile on" gives control back to the fermentation profile of the PLC | Cellar |
| Change the temperature or carbonation of a bright tank | Use the "set °F" and "vol" boxes under the tank | Cellar |
| Change the fermentation profile for all fermenters | Write `CEL.PROFILE.TC01.SP` (fermenting, 66), `TC02.SP` (rest, 70), `TC03.SP` (crash, 34), and `KC01.SP` (rest minutes, 180) | Cellar points |
| Brew | Press the Start button ([Brew a batch](#brew-a-batch)) | Batch |
| Run one brewhouse phase manually. Do this only while the Batch view says Idle | Write `BH.BATCH.XC01.PHASE` (1 to 10). Then write `BH.BATCH.XC01.CMD` (1 start, 2 hold, 3 restart, 4 abort, 5 reset) | Brewhouse points |
| Start a cleaning (CIP) circuit | Type the circuit (1 to 7) in the Circuit box. Then press Start. Hold, Resume, and Abort are next to it (`UTL.CIP.XC01.CIRCUIT` and `XC01.CMD`). If the return flow of a wash does not prove itself in 30 s, the PLC holds the wash with its pumps off. The screen then shows "Held: return flow lost", which is an alarm. Correct the route. Press Hold. Then press Resume | Utilities |
| Start or stop the packaging line outside its shift | Press Reset. Then press Start. To stop the line, press Stop (`PKG.LINE.PACKML.CMD` 1, 2, 3) | Packaging |
| Keep the packaging line stopped during its shift | Press Hold. To release the line, press Unhold. A Stop lasts about two minutes, because the operator of the line starts the line again | Packaging |
| Change the speed of the line | Use the "Speed setpoint" box | Packaging |
| Mark a vessel entry permit | Write `CEL.ENTRY.HS01.ENTRY` to `HS06`, `BH.ENTRY.HS01.ENTRY`, or `HS02`. The Cellar and Brewhouse screens show which vessels are locked | Cellar points, Brewhouse points |
| Reset the CO₂ supply shutoff after a gas alarm | Press Reset in the CO₂ supply panel (`UTL.CO2.XC01.RESET`). The PLC accepts the reset after the detection relay was healthy for 60 s | Utilities |
| Change the demand target, or mark tomorrow as a brew day | Use the "Target kW" box. Use Set and Clear next to "Brew day" | Power |

The plant also runs on a clock. It starts on a Wednesday at 04:30 (simulated
local time). Brew days are Monday and Wednesday at 05:00. The packaging shift
is 06:00 to 14:30 on weekdays. The power PLC exercises the generator on
Wednesdays at 10:00. The cellar crew racks finished fermenters, keeps the
serving tank full, and cleans empty vessels when the plant needs it.

No person has to start the packaging line. Its operator is a part of the
simulation. At 06:00, the operator presses Reset and Start at the panel of the
line. At 14:30, the operator presses Stop. During the shift, the operator
starts the line again after it stood stopped for two minutes.

Fermentation runs 48 times faster than real time, so a two-week fermentation
takes about seven hours. All other processes run in real time.

### Brew a batch

This section applies to `lite` and `full`. scada-fuxa runs the batch engine.
The engine takes the brewhouse PLC through a brew, one phase after the other,
with the numbers of the recipe. The phases are: mill, heat the liquor, mash in,
two mash steps, vorlauf, lauter, boil, whirlpool, knockout to a fermenter, and
ready for cleaning. Open the Batch view. Then sign in as `operator`.

| Button | What it does |
|---|---|
| Start | Adds one brew to the queue. The brew starts when the brewhouse PLC is idle, no brewhouse vessel has an entry permit, and a fermenter is empty |
| Hold | Pauses at the next safe point. The phase that runs stops its equipment and keeps the time that it served |
| Restart | Continues a held batch |
| Abort | Stops the phase, empties the queue, and ends the batch as Aborted |
| Reset | Acknowledges a Complete or Aborted batch. It also clears the queue and the alarms of the engine |

- Status tells you what the batch does, or why it waits. A refused start stays
  in the queue and begins when the cause is gone.
- One brew takes five to six hours. A brew day is two brews into one fermenter.
- Brew days start automatically. At 05:00 on Mondays and Wednesdays (simulated
  time), the plant asks for two brews. This is the same as a press of the
  request button on the brewhouse panel. The `start-batch` event
  ([Run an exercise](#run-an-exercise)) does the same. The first request comes
  30 minutes after the range starts.
- If scada-fuxa is down, a request waits at the panel. FUXA takes the request
  when it is up again. If FUXA restarts in the middle of a batch, it continues
  the batch from the same point.
- If a phase takes too long, or a PLC stops answering, the engine holds the
  batch and shows an alarm on the view. Correct the cause. Then press Restart
  or Abort.
- Do not change the phase and command tags while a batch runs. The engine holds
  the batch if the PLC does something that the engine did not ask for.

### Building automation (bas-fuxa)

Open `http://localhost:11882`. The Building screen is a plan of the building.
It shows the taproom with its rooftop unit, the cold room, and the brewhouse
hood. It also shows the cellar from above, with its four CO₂ sensors and two
purge fans. Next to the cellar are the three CO₂ tiers, with the action of each
tier. The buttons for the purge fans and the horns are there too, with the
sensors of the other areas.

You read the screen in the same way as the plant screens (see above). The
"Building points" view lists each tag. bas-fuxa reads three BACnet controllers
(`bas-rtu-taproom`, `bas-coldroom`, `bas-vent`). When you sign in as
`operator`, you can command their outputs and setpoints.

A command overrides the logic of the controller until a person releases it.
FUXA cannot release a command, but the engineering workstation can (see below).
The scada-fuxa host reads the same CO₂ values through the firewall and does not
write them.

### The operator stations (hmi-brewhouse, hmi-packaging)

Each station is a desktop that opens one FUXA view on the full screen. To see a
station, click the screenshot of its VM in the phenix page. A station signs in
as `operator` about a minute after scada-fuxa is up. As a result, you can write
values and press buttons there with no login. A station renews its sign-in
automatically about every 8 hours, and the view reloads at that time.

To see if a station is signed in, look at the account icon at the top right. A
signed-in station has an orange icon with `operator` next to it. A station that
is not signed in has a white icon with no name. On the console of the station,
`cat /run/cm-kiosk/status` prints `state=operator`, or `state=guest` with the
cause.

A station is always the operator. If you sign in there with a different
account, the station signs in as `operator` again in a few seconds. To work as
`admin`, use your own browser.

### PLCs and field devices, from the engineering workstation

The engineering workstation `ews` (10.20.30.12) has the tools. You can open its
screen from the phenix page. Click its screenshot, and you are `root`. As an
alternative, create a port forward to its port 3389. Then use an RDP client as
a technician. [Vendor remote access](#vendor-remote-access) has the account.

| Task | Command on the EWS |
|---|---|
| PLC status | `python3 -I /usr/local/lib/cm-ews/cm_plc_tool.py status --plc plc-cellar --host 10.20.10.12`. The output must be `STATUS:RUNNING` |
| Read a Modbus value. The example reads the temperature of fermenter 101, and 6600 is 66.00 °F | `mbpoll -m tcp -a 1 -0 -1 -t 3 -r 101 10.20.10.12` |
| Write a Modbus setpoint | `mbpoll -m tcp -a 1 -0 -1 -t 4 -r 106 10.20.10.12 6400` |
| Read an OPC UA value from plc-brewhouse (1680 is 168.0 °F) | `/opt/ews-venv/bin/uaread -u opc.tcp://10.20.10.11:4840/openplc/opcua -n "ns=2;s=BH.HLT.TT01.PV"` |
| Load a PLC program again | `python3 -I /usr/local/lib/cm-ews/cm_plc_tool.py upload --plc plc-cellar --host 10.20.10.12 --zip /opt/cherrymountain/ews/programs/plc-cellar.zip`. The PLC compiles for a few minutes |
| Read and command a BACnet point | Use the BAC0 example in [phenix-injects/otsim/README.md](phenix-injects/otsim/README.md#check-it-on-a-running-range) |

| PLC | Address | Protocol to SCADA |
|---|---|---|
| plc-brewhouse | 10.20.10.11 | OPC UA, port 4840 |
| plc-cellar | 10.20.10.12 | Modbus TCP, port 502 |
| plc-packaging | 10.20.10.13 | S7, port 102. Read it through FUXA, because the EWS has no S7 tool |
| plc-utilities | 10.20.10.14 | Modbus TCP, port 502 |
| plc-power | 10.20.10.15 | Modbus TCP, port 502 |

OpenPLC has no web page of its own in this version. It has only an API at
`https://<plc>:8443/api` (user `admin`). Expect an alert when you write from
the EWS to a life-safety point. The detections of the SOC monitor exactly that
type of write.

### The historian

Open `http://localhost:13000` (Grafana, no login needed to look). The starter
dashboard graphs the temperatures and extract of the fermenters, from the data
that FUXA records to InfluxDB. Its PackTags panel stays empty until the MQTT
publisher of FUXA is built. A daily task copies the production figures that a
brewery must keep into a separate bucket, `records`. InfluxDB is on port 8086
of the historian, and MQTT is on port 1883. If you want them on your computer,
create a port forward.

### Vendor remote access

This section applies to `full` only. Ridgeline Process Controls is the
integrator. It reaches the plant in the same way as a real vendor. The path
starts with a WireGuard tunnel from the internet side to `dmz-vpn`. Then it
goes through `dmz-jump`, the Guacamole jump host (a host that relays remote
sessions). It ends at the EWS.

1. Open the vendor-ws screen from the phenix page.
2. Make sure that the tunnel is up. `wg show wg0` must list a recent handshake.
   If it does not, run `systemctl restart wg-quick@wg0`.
3. Get the one-time code of the technician. Run
   `python3 -I /usr/local/lib/cm-desktop/cm_vendor_session.py --totp`. The
   command prints the account and a code that is valid for a few seconds.
4. In the browser on vendor-ws, open `https://10.10.20.11/`. Accept the
   certificate. Sign in as `ridgeline-akim` with `vendor_guac_password`. Then
   type the code.
5. Open the connection `ews-rdp-ridgeline-akim` (a desktop) or
   `ews-ssh-ridgeline-akim` (a shell). Guacamole logs in to the EWS for you. It
   records the session on dmz-jump under `/var/lib/cm-guacamole/recordings`.
6. Work on the EWS as shown above. The account of the technician can run
   `cm_plc_tool.py`.

`cmadmin` (with `guacamole_password`) administers Guacamole. Its first sign-in
shows a QR code to enroll an app for one-time codes.

### The SOC: Malcolm and Wazuh

This section applies to `full` only.

Malcolm is at `https://localhost:18443`, user `malcolm` with
`malcolm_password`. Use `/dashboards/` for the Zeek and Suricata dashboards.
Look there for Modbus, BACnet, DNP3, and OPC UA. Use `/arkime/` for sessions
and packets. Mirrored packets carry their VLAN tag, so packet filters need
`vlan and ...`.

To see if the capture is live, run `cm-malcolm-health` on the malcolm console.
It must print `HEALTHY` with a packet count, a count of Zeek records, and a
count of Arkime sessions. Zeek and Suricata show traffic in seconds. Arkime
indexes the capture in 2-minute files, so a session appears there a few minutes
later. `cm-malcolm-selftest` plays a test capture through Zeek and Suricata.

Wazuh is at `https://localhost:18444`, user `admin` with `wazuh_password`. The
rules of the range have the ids 100100 to 100413. In Threat Hunting, filter on
`rule.groups: cm_vendor`, `cm_wireguard`, or `cm_firewall`. Agents appear in
the groups `ops`, `hmi`, `bas`, `dmz`, and `soc`.

On the wazuh console, `cm-wazuh-health` makes sure that the setup finished and
that Filebeat reaches the indexer. It also examines the services and the
agents. `cm-wazuh-selftest` sends sample log lines through the rules.

Expect firewall alerts (`cm_firewall`, rules 100100 to 100104) about ten
minutes after a start. Expect them again in the vendor-maintenance run. The
state of health check tries each blocked path on purpose, and the firewalls log
each attempt.

soc-analyst is the desktop of the analyst. When Malcolm and Wazuh are up, it
opens them in a browser automatically.

soc-purple is a Kali Purple desktop. Open its console in the phenix UI. It has
Wireshark, Suricata, `peat`, and parseus. The parseus tool installs itself in
the first minutes (`systemctl status parseus-install`, then
`https://127.0.0.1/` in its own browser). The soc-purple VM reaches Malcolm
(`https://malcolm`) and Wazuh (`https://wazuh`), and it reaches no host in the
plant. Give it data: a pcap from Arkime, or files.

### The white cell: see and drive the truth

This section applies to `lite` and `full`. The white cell is the staff that
runs the exercise. These tools show the real state of the plant, independent of
what the operators and the SOC see. Do not show them to trainees.

- cm-viz, at `http://localhost:18090`, is a 3D view with three scenes. Select a
  scene with `?scene=cellar` (cellar CO₂ and life safety), `?scene=fermenters`,
  or `?scene=power`. `/svg.html` is the same view without 3D graphics.
- The event API is at `http://localhost:18080/api/v1/`. It has `health`, `kpis`
  (batches, bottles, kegs, energy, demand, compliance figures), `state` (each
  value), and `events` (the injected events).
  [Run an exercise](#run-an-exercise) shows how to post events.
- The ground truth (the records of the simulation itself) is at
  `http://localhost:13001`. The dashboard "CherryMountain ground truth" graphs
  those records and lists injected events. The records are in OpenSearch at
  `http://localhost:19200`, in indexes with the name
  `cherrymountain-truth-<part>-<date>`. Each record has the wall-clock time and
  the simulated time, so you can align it with Malcolm and Wazuh.

## Run an exercise

SCORCH is the phenix app that runs a prepared scenario. For a scripted run
(`full` only), SCORCH runs the scenario and collects the evidence under
`/phenix/images/<experiment>/files/scorch/run-<N>/`. The Files tab of the
experiment shows the same directory. Run this command on the phenix host:

```shell
docker exec -it phenix phenix experiment scorch cherrymountain-brewery --run 2
```

| Run | Name | What it does |
|---:|---|---|
| 0 | `vendor-maintenance` | A scripted vendor session uploads a PLC program again. The run then makes sure that Wazuh and Malcolm saw it. It pauses for the analyst. Type `exit` to continue, so the run needs `-it` |
| 1 | `baseline-brew-day` | Requests a brew day and records its first half hour, with packet captures and exports |
| 2 | `utility-outage` | Opens the utility breaker for ten minutes. Then it makes sure that the generator carries the emergency loads |
| 3 | `cellar-co2-leak` | Releases CO₂ at a fermenter. Then it examines the purge fans and the alarm |
| 4 | `packaging-shift` | Jams packaging machines on a degraded field network |

A run blocks until it is done. Most runs take 30 to 45 minutes.

To inject events manually (`lite` and `full`), post to the event API. With the
tunneler of step 8 active, run these commands on your computer:

```shell
api=http://localhost:18080/api/v1
curl -s -X POST -d '{"event":"start-batch","brews":1}' $api/events
curl -s -X POST -d '{"event":"utility-breaker","action":"open"}' $api/events
curl -s -X POST -d '{"event":"utility-breaker","action":"close"}' $api/events
curl -s -X POST -d '{"event":"co2-release","fv":"FV-102","rate":"high","duration":600}' $api/events
curl -s -X POST -d '{"event":"co2-leak","zone":"tank","rate":"high","duration":600}' $api/events
curl -s -X POST -d '{"event":"jam","machine":"labeler","duration":300}' $api/events
curl -s -X POST -d '{"event":"chiller-fault","action":"fail","compressor":1}' $api/events
curl -s $api/kpis
```

A good post answers 202. A bad post answers 400 and gives the cause. The events
are `start-batch`, `utility-breaker`, `genset-exercise`, `chiller-fault`,
`co2-release`, `co2-leak`, `gas-detection`, `refrigerant-leak`, `bess-thermal`,
`jam`, `cip-panel`, and `weather-step`. [sim/README.md](sim/README.md) lists
each parameter.

Then compare the real events (cm-viz, ground truth) with what the trainees saw
(FUXA, Malcolm, Wazuh).

The rules of play are as follows. Keep players away from the simulation network
(MGMT), the VM consoles, and the phenix host. Those three are out of bounds for
players. Keep the phenix host on a network that you trust. A person who can
reach the phenix host can reach each forward, and that includes the event API.

## The multi-site variant (`fleet`)

The `fleet` variant is not built yet. It is four breweries and a head office. A
site-to-site VPN over a mock internet of five BGP networks joins them. The
variant is a plan with tested parts, not a range that you can start.
The file [docs/multisite/PLANS.md](docs/multisite/PLANS.md) has the design and
its decisions. The file
[docs/multisite/REGIONS.md](docs/multisite/REGIONS.md) describes the four
places.

This directory has no `phenix-fleet.yml`. This section shows how to run the
four parts that exist. Then it shows how the fleet is planned to start.

| Part | What it runs | You need | Time |
|---|---|---|---|
| [A](#a-the-whole-wan-on-one-linux-host) | The whole WAN as network namespaces: ten routers and a host at each site | One Linux host and root. No phenix and no VMs | 10 min |
| [B](#b-the-wans-core-on-vyos-routers-under-phenix) | The core of the WAN on four VyOS routers under phenix and minimega | GitHub Actions on this repository | 20 to 45 min |
| [C](#c-the-four-sites-as-phenix-topology-files) | The four sites as phenix topology files, with their sizes | Python and a phenix checkout | 1 min |
| [D](#d-the-whole-wan-as-a-phenix-variant-wan) | The whole WAN as a phenix variant: ten VyOS routers and five hosts. Nobody ran this part yet | A phenix host with the draft `vrouter` change, and two images | Not measured |

### A. The whole WAN on one Linux host

This part is the lab of [spikes/wan-bgp](spikes/wan-bgp/README.md). It has the
transit carrier, four ISPs, and the head office on two of those ISPs. It also
has three site edges, the five tunnels with BGP and BFD inside, and one host
behind each edge. Each router is a network namespace with its own FRR. The
tunnels are WireGuard in this lab. The plan for the range is route-based IPsec,
which the next part runs.

Use a host or a container whose own FRR you do not need. The lab works inside
its own namespaces. It also writes the FRR files of each router under
`/etc/frr/<router>` and `/var/run/frr/<router>`. It removes those files at
teardown.

1. Install the necessary packages on the host (Debian or Ubuntu):

   ```shell
   sudo apt-get install iproute2 frr wireguard-tools wireguard-go nftables traceroute iputils-ping
   python3 -m pip install pydantic     # Python 3.12 or later
   ```

2. Run the lab from a checkout of this repository. The command builds the lab,
   runs its checks and its faults, and removes it. It takes about six minutes
   and prints `PASS` or `FAIL` for each of its 30 checks:

   ```shell
   cd cherrymountain-brewery/spikes/wan-bgp
   sudo -E python3 netns_lab.py
   ```

3. To examine the lab, keep it up:

   ```shell
   sudo -E python3 netns_lab.py --keep          # with the checks
   sudo -E python3 netns_lab.py --up-only       # without them
   ```

   | To see | Run |
   |---|---|
   | The routers and hosts | `sudo ip netns list` |
   | The BGP sessions of the head office | `sudo ip netns exec fr-fw-edge vtysh -N fr-fw-edge -c 'show bgp ipv4 unicast summary'` |
   | The routes of a site through its tunnels | `sudo ip netns exec fc-fw-edge vtysh -N fc-fw-edge -c 'show ip route bgp'` |
   | One of the tunnels of a site (`wgs-fc-a` is the primary and `wgs-fc-b` is the backup). With `wireguard-go`, `wg show` alone lists each tunnel of the lab | `sudo ip netns exec fc-fw-edge wg show wgs-fc-a` |
   | A host at Fort Collins that reaches the head office | `sudo ip netns exec fc-historian ping -c 3 10.20.30.11` |
   | The path between two sites, through the head office | `sudo ip netns exec fc-historian traceroute -n 192.168.20.11` |

4. Remove the lab:

   ```shell
   sudo -E python3 netns_lab.py --down
   ```

The run writes `summary.md` under `evidence/wan-bgp-netns`. It also writes the
configuration and the final tables of each router there. To write to a
different directory, set `CM_EVIDENCE_DIR`. `python3 vyos_sketch.py` writes the
same WAN as VyOS commands in `vyos-sketch/`, one file for each router.

### B. The WAN's core on VyOS routers under phenix

Four VyOS routers and two hosts boot under phenix and minimega. The routers are
two ISPs, the edge of the head office, and the Fort Collins edge. At first
boot, each router takes its BGP, BFD, and tunnels from the `config` list of the
phenix `vrouter` app. That list needs a change to phenix that is still a draft
([GhostofGoes/sceptre-phenix#27](https://github.com/GhostofGoes/sceptre-phenix/pull/27)).
One job builds the tunnels with WireGuard, and one job builds them with
route-based IPsec.

This part runs in GitHub Actions, as the `wan-bgp-vyos` job of the workflow
[cherrymountain-spikes.yml](../.github/workflows/cherrymountain-spikes.yml).
The job builds the VyOS image, patches phenix, and boots the routers. Then it
runs 39 checks and four faults. It uploads `evidence-wan-bgp-vyos-wireguard`
and `evidence-wan-bgp-vyos-vti`.

- The runs so far started with a push to the branch `test-wan-bgp-vyos`. That
  branch is the feature branch, with the push trigger of the workflow extended
  to it. The last run is
  [run 33](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/45131775836),
  with 39 of 39 checks for each type of tunnel.
- The workflow also has a manual trigger with a `spikes` input. Set the input
  to `wan-bgp-vyos`. The runs above did not use this trigger.
- No person tried a run on a different phenix host. The steps of the job are
  the recipe. The action [.github/actions/cm-lab](../.github/actions/cm-lab)
  prepares the host. The patch in `spikes/wan-bgp/patches/` goes onto phenix.
  `sudo -E python vyos_ci.py` in `spikes/wan-bgp` runs the experiment
  `spike-wan-bgp`.

A run takes about 20 minutes when the VyOS image of the month is in the cache.
It takes about 45 minutes when the job builds the image.
[spikes/wan-bgp/README.md](spikes/wan-bgp/README.md#the-same-wan-on-vyos-routers)
has the results of each run.

### C. The four sites as phenix topology files

The kit in [spikes/site-profiles](spikes/site-profiles/README.md) builds the
nodes of each site from the single-site plant and a short description of the
site. It also sizes the range for a cluster of three nodes. Run these commands
on a host that has Python and a sceptre-phenix checkout:

```shell
cd cherrymountain-brewery/spikes/site-profiles
python3 profiles.py --schema /phenix/schemas --out /tmp/fleet
```

`--schema` is the `schemas/` directory of a sceptre-phenix checkout. The kit
also needs `pyyaml`, `pydantic`, and `check-jsonschema` on `PATH`. It writes
these files:

| File | What it is |
|---|---|
| `topology-site-fr.yml`, `-ep.yml`, `-cs.yml`, `-fc.yml` | The nodes of each site: 39, 35, 26, and 19 with the sensors |
| `phenix-fleet.yml` | A workflow with a VLAN ID for each of the 54 aliases and a cluster node for each site VM |

Do not apply that workflow. The files are for counts and for tests of names and
addresses. They have no routes, no firewall rules, no internet, and no
scenario. The workflow names a topology that does not exist.

### D. The whole WAN as a phenix variant (`wan`)

The `wan` variant is the mock internet and the company's WAN, with no plant.
It has the two routers of the transit carrier, four ISPs, the edge of the head
office, and the edges of the three other sites. It also has one host behind
each edge and one host on the internet. That is 15 VMs, 25 vCPUs, and 25 GB.

Nobody ran this variant yet. Its files are generated and tested offline. The
four routers of part B commit the same configuration lists in CI. The other
six routers, the transit carrier, and the SoH tests are not proven on VMs. The
first run is stage 2 of [PLANS.md §8](docs/multisite/PLANS.md#8-stages).

The tool [tools/wangen](tools/wangen/README.md) writes the three files of the
variant from `wan/wan.yaml`: `phenix-wan.yml`,
`phenix-configs/topology-wan.yml`, and `phenix-configs/scenario-wan.yml`.

Before you start, make sure that the phenix host has these items:

- A phenix that accepts the `config` list of the `vrouter` app. That change is
  a draft
  ([GhostofGoes/sceptre-phenix#27](https://github.com/GhostofGoes/sceptre-phenix/pull/27)).
  Without it, the routers boot with addresses only, and no SoH test passes.
- The images `vyos.qc2` and `cm-svc.qc2` in `/phenix/images`
  ([step 5](#5-build-the-vm-images)).
- No other variant in the `running` state. The `wan` variant uses the VLAN IDs
  of the fleet, 100 to 599.

1. On the phenix host, check the workflow without a start:

   ```shell
   T=/phenix/topologies/cherrymountain-brewery
   docker exec phenix phenix workflow apply -n -b cherrymountain-wan -c phenix-wan.yml $T
   ```

2. Start the variant:

   ```shell
   docker exec phenix phenix workflow apply -b cherrymountain-wan -c phenix-wan.yml $T
   ```

   The experiment is `cherrymountain-wan`, on the bridge `cherrymtn-wan`.

3. Make sure that the VMs run, as in [step 7](#7-wait-then-check-health):

   ```shell
   docker exec phenix phenix vm info cherrymountain-wan
   ```

4. Open the SoH page at `http://<host>:3000/soh/cherrymountain-wan`. The soh
   app starts its tests five minutes after the start. Each of the four site
   hosts pings through the VPN, and pings `inet`, the host on the internet.

5. To examine a router, open its console in the phenix UI
   ([step 8](#8-connect-from-your-own-computer)) and run a command in it:

   | To see | Command in the router |
   |---|---|
   | The BGP sessions | `vtysh -c 'show bgp ipv4 unicast summary'` |
   | The IPsec tunnels | `sudo swanctl --list-sas` |
   | The default routes of a site, one for each uplink | `ip -4 route show default` |
   | The packets that the source filter of the head office dropped | `sudo journalctl -k \| grep ipv4-FWD-filter` |

To change the WAN, edit `wan/wan.yaml`, run `python -m wangen` in
`tools/wangen`, and apply the workflow again. `vpn.kind` selects route-based
IPsec (`vti`) or WireGuard. `vpn.hub_opens` selects which end opens the IPsec
tunnels.

The variant has no fault runs yet. Part B runs the faults on four routers.

### How the fleet is planned to start

None of this works yet. This section tells you what a start will need.
[PLANS.md §8](docs/multisite/PLANS.md#8-stages) has the stages.

| Before the fleet can start | State |
|---|---|
| The first run of the single site on a phenix host (stage 0) | Not done. Start with `ci`, as [step 6](#6-start-the-range) says |
| A cluster of three minimega nodes. A full-size packet must go between two nodes on the bridge of the fleet (stage C) | Not run. The test is a ping of 1472 bytes with fragmentation not permitted. Do the test after `phenix doctor` passes its bridge-trunk check on each node |
| A phenix that accepts the `vrouter` `config` list | A draft pull request |
| A `wan` variant: the mock internet and the four site edges, 15 VMs (stage 2) | Built and not run on VMs. Part D shows how to start it |
| The site files, the cross-site firewall rules, and a sensor image (stages 3 and 4) | Not built. Part C is the start |

When those items exist, the plan is to start the fleet with one command, as for
the other variants. You will run it on the head node of the cluster:

```shell
# Planned. These files do not exist yet.
T=/phenix/topologies/cherrymountain-brewery
docker exec phenix phenix workflow apply -n -b cherrymountain-fleet -c phenix-fleet.yml $T     # check only
docker exec phenix phenix workflow apply    -b cherrymountain-fleet -c phenix-fleet.yml $T
```

- The experiment will be `cherrymountain-fleet`, on the bridge
  `cherrymtn-fleet`. It will have 136 VMs, 195 vCPUs, and 394 GB on three nodes
  of 48 cores and 512 GB.
- The workflow will name a node for each VM. It will set `deployMode: all`, so
  that the head node also runs VMs.
- The fleet will use VLAN IDs 100 to 599, so no other variant can run at the
  same time.

## Stop, reset, and clean up

Run these commands on the phenix host:

```shell
docker exec phenix phenix experiment stop cherrymountain-lite      # stop; exports FUXA history and an InfluxDB backup first
docker exec phenix phenix experiment start cherrymountain-lite     # start again with a fresh plant and the same passwords
docker exec phenix phenix experiment delete cherrymountain-lite    # remove it, passwords included
```

- A stop and start gives a new plant. phenix builds the disk of each VM again
  from its image. As a result, the simulation starts again, and the PLCs load
  and compile their programs again.
- All data stored in a VM is gone after a stop and start. That data is the data
  of the historian, the batch count of the batch engine, the Malcolm and Wazuh
  data, and the recordings of Guacamole. The passwords stay. Before you stop,
  export the data that you want to keep.
- A stop puts its exports in
  `/phenix/images/<experiment>/files/cherrymountain/`.
- After a `phenix experiment start` from the command line, the five-minute
  status refresh of the app does not run. Start with `workflow apply` or from
  the phenix page. As an alternative, refresh manually:
  `docker exec phenix phenix experiment trigger running <experiment> cherrymountain`.
- The simulation ends after 7 days (24 hours in `ci`). For a longer exercise,
  stop and start the experiment.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `... is a Git LFS pointer; run git lfs pull` | Run `git -C /phenix/topologies lfs pull`. Then apply again |
| No `/dev/kvm` | Run `kvm-ok`. On a cloud VM, turn on nested virtualization |
| An image build fails | Read the end of `/phenix/images/<name>.log` for the URL or package that failed. Then try again. The builds download from Ubuntu, GitHub, PyPI, npm, and vendor repositories |
| The VyOS build fails | The build uses the rolling branch of VyOS, which sometimes breaks for a day. Try again later. Keep a `vyos.qc2` that works |
| `workflow apply` says a second experiment cannot use the bridge or VLANs | A different variant runs. Stop it first |
| `phenix-tunneler` lists nothing | The experiment does not run, or phenix is not the fork (the fork creates the forwards). Run `docker exec phenix phenix version` |
| A page does not answer on its local port | The service of that VM is not ready. Open the console of the VM. Then examine the service |
| SoH fails on a PLC | On the console of the PLC, `journalctl -u cm-openplc-provision` shows the upload and the compile |
| No value changes in FUXA | Run `curl -s http://localhost:18080/api/v1/health`. If it says `starting`, a part of the simulation did not join. On sim-ctl, read `/var/log/helics-root-broker.log`. On the device, read `journalctl -u ot-sim`. On sim-ctl or on the device, `nft list table inet cm_mgmt` shows whether the firewall of the simulation network drops packets |
| bas-fuxa shows no values | On a `bas-*` console, `journalctl -u ot-sim \| grep bacnet-server` must say "first values from the message bus". [phenix-injects/otsim/README.md](phenix-injects/otsim/README.md) has more |
| FUXA does not accept a value or a button | In your own browser, you are not signed in. Sign in as `operator` ([step 9](#9-passwords)). On an operator station, its own sign-in did not work yet. `cat /run/cm-kiosk/status` on its console gives the cause. The station keeps trying, and the SoH check `kiosk-operator-session` shows the same state |
| A brew does not start | Read Status on the Batch view. `start refused` names the cause. The brewhouse PLC is not idle (press Reset), or a vessel has an entry permit. No fermenter is empty, or the link to the PLC is down. If the view shows no data, run `journalctl -u fuxa \| grep cmBatchEngine` on the scada-fuxa console |
| Malcolm shows no traffic | Run `cm-malcolm-health` on its console. Read the status of the app ([step 7](#7-wait-then-check-health)). If the status does not say `"dfDefault": "set"`, set the option manually: `docker exec minimega sh -c 'for p in $(ovs-vsctl --bare --columns=name find interface type=gre); do ovs-vsctl set interface "$p" options:df_default=false; done'` |
| SCORCH run 0 fails at `analyst-review` | Run it with `docker exec -it` |

## Known limits

Prepare for these limits. [DESIGN.md §19](docs/DESIGN.md#19-risks-and-open-questions)
has the full list.

No part of the range ran on VMs. The first boot of each image is the first real
test of its provisioning scripts. Some parts ran for real outside a VM:

- The batch engine, its view, and the operator sign-in ran in a FUXA server.
- The BACnet controllers ran with BACnet clients.
- Each PLC program went through the compiler.
- The first-boot setup of Malcolm ran, and its capture containers ran on a
  stand-in for the mirror.
- The Guacamole stack ran.

On a real range, make sure of these three items first:

- FUXA can write to the brewhouse PLC over OPC UA.
- Wazuh takes the `admin` password from `secrets.json`.
- The drops of the firewalls reach Wazuh. The script that turns on the logging
  of the routers did not run on VyOS yet. On a router,
  `cat /tmp/cm-vyos-config.done` shows an empty file when the script did each
  step.

The bottling line waits for beer. The filler holds when BBT-202 is empty, and
it starts again after the crew fills the tank. The line empties a tank in about
3.5 hours, and two brew days make two tanks a week. Expect the line to stand
held for much of the week.

The time limits of the batch engine are estimates. If the engine holds a
healthy phase for "phase timed out", press Restart. Then tell us which phase it
was.

## Where to go next

- [DESIGN.md §15.9](docs/DESIGN.md#159-operations): the operations runbook,
  failure modes, and the security of the range itself.
- [§10](docs/DESIGN.md#10-network-architecture): zones, addresses, and which
  hosts can talk to which. [§14](docs/DESIGN.md#14-blue-team-monitoring): the
  detections.
- [§15.6](docs/DESIGN.md#156-scorch-pipelines): the SCORCH runs.
  [§15.10](docs/DESIGN.md#1510-white-cell-3d-view-cm-viz): the 3D view.
- [sim/README.md](sim/README.md): the simulation and each event.
- [The README of the `cherrymountain` app](https://github.com/GhostofGoes/sceptre-phenix-apps/blob/feat-cherrymountain-apps/src/python/phenix_apps/apps/cherrymountain/README.md):
  the phenix app of the range and its status.
- After a change to `plant/plant.yaml`: "Regenerate and test" in
  [README.md](README.md#regenerate-and-test).
- [docs/multisite/PLANS.md](docs/multisite/PLANS.md): the plans for the
  multi-site `fleet` variant. [REGIONS.md](docs/multisite/REGIONS.md): the
  utilities and rules of its four places.
