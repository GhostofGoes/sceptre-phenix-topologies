# CherryMountain Brewery: user guide

This guide takes you from an empty host to a running CherryMountain range, and
then shows you how to reach and use each part of it. Follow it top to bottom
the first time. [README.md](README.md) says what is in this directory, and
[DESIGN.md](DESIGN.md) explains why the range is built the way it is.

> **Read this first.** The range has **not booted on a real phenix host yet**.
> The simulation, the apps, the PLC programs, and the network rules are tested
> offline, and parts of it have run in Docker and on loopback. Every step below
> comes from the files and from the phenix, minimega, and vendor source code,
> but nobody has run it end to end on VMs. Expect to fix things on the first
> boot, start with the small `ci` variant, and see [Known limits](#known-limits).

## The path in one look

| Step | What you do | Time, roughly |
|---|---|---|
| [1](#1-pick-a-variant) | Pick a variant that fits your host | |
| [2](#2-prepare-the-host) | Install Docker, Git LFS, and Go; check KVM | 15 min |
| [3](#3-get-the-code) | Clone four repositories | 5 min |
| [4](#4-build-and-start-phenix) | Build the phenix container and start it | 30 min |
| [5](#5-build-the-vm-images) | Build the VM images (4 for `ci`, 13 for `full`) | 1 h to several hours |
| [6](#6-start-the-range) | Apply the workflow: it creates and starts the experiment | 5 min |
| [7](#7-wait-then-check-health) | Wait for first boot, then check health | 15 to 30 min |
| [8](#8-connect-from-your-own-computer) | Run `phenix-tunneler` on your computer and open the web pages | 5 min |
| [9](#9-passwords) | Look up the passwords | |
| [10](#10-a-tour-use-each-part) | Use each part: SCADA, PLCs, vendor access, SOC, white cell | |

## 1. Pick a variant

The range is a notional Colorado microbrewery: a brewhouse, cellar, packaging
line, utilities, plant power, and building automation, with FUXA as the SCADA
and HMI, OpenPLC for the PLCs, ot-sim for the field devices, and a HELICS
simulation of the physics behind them.

| Variant | What it runs | VMs | vCPUs | RAM | Use it for |
|---|---|---:|---:|---:|---|
| `ci` | The cellar only: one simulation node pair, one remote I/O, one PLC, one FUXA | 5 | 7 | 8 GB | A first boot, and a laptop |
| `lite` | The whole plant and its network, without the SOC and the vendor path | 37 | 48 | 71 GB | Process and network work |
| `full` | Everything, with Malcolm, Wazuh, WireGuard, and Guacamole | 43 | 67 | 113 GB | Exercises with a blue team |

Run **one variant at a time**. They use the same VLAN numbers, and minimega
refuses the second one. A stopped experiment of another variant may stay.

## 2. Prepare the host

You need one machine with:

- **Ubuntu 24.04 on x86-64 with `/dev/kvm`.** Check with `kvm-ok` (package
  `cpu-checker`). On a cloud VM, turn on nested virtualization.
- **CPU and memory for the variant.** 4 cores and 16 GB run `ci`. `full` is
  sized for 32 or more cores and 128 GB: most VMs idle, so its 67 vCPUs are
  overcommitted, but its 113 GB of VM memory is not.
- **Disk.** About 150 GB free under `/phenix` for all images and the running
  VMs. The four `ci` images need about 20 GB.
- **Software.** Docker with the Compose plugin, Git, [Git LFS](https://git-lfs.com/),
  and Go 1.24 or later.
- **Internet while you build.** The running range needs none.

```shell
sudo apt update
sudo apt install -y git git-lfs cpu-checker golang-go
git lfs install
kvm-ok                      # must say "KVM acceleration can be used"
docker compose version      # install Docker Engine first if this fails
go version                  # 1.24 or later; if older, install Go from go.dev
```

## 3. Get the code

The range needs a **fork of phenix** (branch `ci-e2e-minimega`). Only that fork
keeps the port forwards to the VMs' web pages open for an experiment that is
started from the command line.

```shell
git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix.git
git clone -b ci-e2e-minimega https://github.com/GhostofGoes/sceptre-phenix-images.git
git clone https://github.com/sandia-minimega/minimega.git
git -C minimega checkout "$(cat sceptre-phenix/.github/e2e/minimega.ref)"

sudo mkdir -p /phenix && sudo chown "$USER" /phenix
git clone -b feat-secret-topo https://github.com/GhostofGoes/sceptre-phenix-topologies.git /phenix/topologies
git -C /phenix/topologies lfs pull
```

Keep the topologies at **`/phenix/topologies`**. The `cherrymountain` app reads
`/phenix/topologies/cherrymountain-brewery` from inside the phenix container.

Check that Git LFS fetched the PLC programs. A file of about 130 bytes is only
a pointer:

```shell
ls -l /phenix/topologies/cherrymountain-brewery/phenix-injects/openplc/plc-cellar.zip   # about 100 KB
```

## 4. Build and start phenix

**Build the phenix image** from the fork, with the fork's apps (about 20
minutes; it needs BuildKit, which current Docker uses by default):

```shell
cd sceptre-phenix
docker build -f docker/Dockerfile \
  --build-arg APPS_REPO=github.com/GhostofGoes/sceptre-phenix-apps \
  --build-arg APPS_BRANCH=ci-e2e-minimega \
  -t phenix:cherrymountain .
```

**Add the range's own app** (`cherrymountain`) and its SCORCH component
(`plantevent`) on top:

```shell
docker build --build-arg PHENIX_IMAGE=phenix:cherrymountain \
  -t cherrymountain-phenix /phenix/topologies/cherrymountain-brewery/apps
```

**Tell Compose to use that image**, then start phenix and minimega. The
override file saves you from editing the fork's `docker-compose.yml`:

```shell
cd docker
cat > docker-compose.override.yml <<'EOF'
services:
  phenix:
    image: cherrymountain-phenix
EOF
docker compose up -d
docker exec phenix phenix experiment apps
```

The last command must list `cherrymountain`, `ot-sim`, `helics`, `mirror`,
`wireguard`, and `protonuke`. The phenix web page is now at
`http://<host>:3000`. It has no login in this setup, so keep the host on a
network you trust.

**Build the two programs that go inside every VM.** `miniccc` is how phenix
reaches into a VM; `protonuke` makes stand-in internet traffic:

```shell
cd ../../minimega
source scripts/env.bash
./scripts/version.bash
CGO_ENABLED=0 go install -ldflags="-s -w" ./cmd/miniccc ./cmd/protonuke
cp bin/miniccc bin/protonuke /phenix/
```

## 5. Build the VM images

Each kind of VM has an image config in `phenix-images/`. A build is the same
three commands for every image. It runs inside the phenix container, needs
internet, and does not need KVM.

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
| `lite` | `cm-svc` `cm-otsim` `cm-sim` `cm-openplc` `cm-fuxa` `cm-historian` `cm-desktop` `cm-ews`, plus VyOS and the two protonuke steps below |
| `full` | Everything for `lite`, then `cm-guacamole` `cm-wireguard` `cm-wazuh` `cm-malcolm` |

```shell
for image in cm-sim cm-otsim cm-openplc cm-fuxa; do build_image "$image"; done    # ci
```

`lite` and `full` need three more things:

```shell
# protonuke, the stand-in for internet traffic
cp /phenix/images/cm-svc.qc2 /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-svc-protonuke.qc2
docker exec phenix phenix image inject-miniexe /phenix/protonuke /phenix/images/cm-desktop.qc2

# the VyOS router image, from the images repository (it finds /phenix/miniccc by itself)
cd sceptre-phenix-images && sudo make vyos && sudo mv vyos.qc2 /phenix/images/
```

Things to know about image builds:

- `cm-malcolm` (64 GB) is the slowest by far. `cm-desktop` and `cm-ews` install
  a desktop and take a while too.
- A build leaves `<name>.tar` (a cache of the base system) and `<name>.log`
  beside the image. Read the log when a build fails. Delete the `.tar` to save
  disk, or after you change an image's package list.
- To rebuild an image, run `docker exec phenix phenix image delete <name>`
  first. `phenix config create` fails when the config already exists.
- `cm-sim` installs the simulation from this repository at the commit named by
  `CM_REF` in `phenix-images/cm-sim.yml`, so that commit must be on GitHub.
- [phenix-images/README.md](phenix-images/README.md) says what is in each image.

## 6. Start the range

One command does the whole job. `phenix workflow apply` stores every config in
`phenix-configs/`, copies `phenix-injects/` to `/phenix/injects/<name>`, and
creates and starts the experiment. Run it from outside the directory.

Start with `ci`. The first line (`-n`) only checks and changes nothing:

```shell
cd /phenix
T=/phenix/topologies/cherrymountain-brewery
docker exec phenix phenix workflow apply -n -b cherrymountain-ci -c phenix-ci.yml $T
docker exec phenix phenix workflow apply    -b cherrymountain-ci -c phenix-ci.yml $T
```

When `ci` works, stop it and move up:

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

- Use `workflow apply`, not `phenix experiment create`. The configs find their
  injected files through a name that only the workflow fills in.
- To apply again over a **running** experiment, add `-f`. Applying over a
  stopped one updates it and starts it.
- A missing image does not stop the start: phenix marks that VM "do not boot"
  and warns. Read the output.

In `full`, Malcolm's packet mirror needs one option on its tunnel port
(`df_default=false`), or full-size packets are lost. The range's app sets it by
itself after every start; step 7 checks it.

## 7. Wait, then check health

The VMs start in layers, and each layer waits for the one before: routers,
the simulation, field devices, PLCs, then FUXA. Every start is a first boot:
phenix builds each VM's disk again from its image, so give it time.

| What | Time after its VM starts |
|---|---|
| Routers (VyOS) | 2 to 3 min |
| Simulation and field devices | 2 to 4 min |
| PLCs (each compiles its program) | 3 to 8 min |
| FUXA | 2 min after its PLCs |
| Wazuh (`full`) | 5 to 10 min |
| Malcolm (`full`) | 10 to 15 min |

Then check, in this order:

1. **The VMs are running.**

   ```shell
   docker exec phenix phenix vm info cherrymountain-ci
   ```

   Or open `http://<host>:3000/experiment/cherrymountain-ci`. Each VM is a
   tile; a tile with a live screenshot has booted.

2. **State of health (SoH).** Open `http://<host>:3000/soh/cherrymountain-ci`.
   The checks start about 10 minutes after the last VM has started, so the page
   stays empty for a while. A first pass that fails only on the PLCs or on
   Malcolm is normal: they were still starting. Run the checks again with:

   ```shell
   docker exec phenix phenix experiment trigger running cherrymountain-ci soh
   ```

3. **The simulation is running** (`lite` and `full`). With the tunneler of
   [step 8](#8-connect-from-your-own-computer) running:

   ```shell
   curl -s http://localhost:18080/api/v1/health
   ```

   `"status": "ok"` with a `sim_time` that advances means every part of the
   simulation has joined. `"starting"` means one has not: see
   [Troubleshooting](#troubleshooting).

4. **What the range's app saw.** It checks every VM's provisioning and reads
   the plant's key figures every five minutes:

   ```shell
   docker exec phenix phenix config get experiment/cherrymountain-ci -o json -p | grep -A40 '"cherrymountain"'
   ```

   Look for `"provisioned": "n/m"` and, per host, `provisioned`, `pending`,
   `failed`, or `unreachable`.

5. **The mirror option is set** (`full`). This should print
   `"dfDefault": "set"`:

   ```shell
   docker exec phenix phenix config get experiment/cherrymountain-brewery -o json -p | grep dfDefault
   ```

If one VM never starts its control connection, every VM that waits for it
stays down and SoH never begins: phenix has no timeout there. Open that VM's
console (step 8) and look.

## 8. Connect from your own computer

The VMs are on closed networks. phenix forwards a list of their web pages for
you, and a small program on your computer, **`phenix-tunneler`**, makes each
one appear on a fixed local port. Your computer only needs to reach the phenix
web page.

1. **Download it.** Open `http://<host>:3000`, click the **Tunneler** tab, and
   download the file for your system. Or fetch it directly:
   `http://<host>:3000/downloads/tunneler/phenix-tunneler-linux-amd64`
   (also `-darwin-arm64`, `-darwin-amd64`, `-windows-amd64.exe`).
2. **Run it and leave it running** (no trailing slash on the address):

   ```shell
   chmod +x phenix-tunneler-linux-amd64
   ./phenix-tunneler-linux-amd64 serve http://<host>:3000
   ```

   It prints `activated local listener on port ...` for each forward.
3. **Open the pages** in your browser:

| Open this on your computer | What it is | Variant | Sign in |
|---|---|---|---|
| `http://localhost:11881` | **scada-fuxa**: process SCADA and HMI | all | none to look; `operator` to operate; `admin` to edit |
| `http://localhost:11882` | **bas-fuxa**: building automation front end | lite, full | same |
| `http://localhost:13000` | **historian**: Grafana with process trends | lite, full | none to look; `admin` |
| `http://localhost:18090` | **cm-viz**: the 3D view of what is really happening | lite, full | none |
| `http://localhost:18080/api/v1/health` | **event API**: drive the plant | lite, full | none |
| `http://localhost:13001` | **ground-truth Grafana** on sim-ctl | lite, full | none to look; `admin` |
| `http://localhost:19200` | **ground-truth OpenSearch** | lite, full | none |
| `https://localhost:18443` | **Malcolm**: network security monitoring | full | `malcolm` |
| `https://localhost:18444` | **Wazuh**: SIEM | full | `admin` |

The brewery's own screens carry its mark, a phoenix over a summit, and its
dark-and-ember colors: FUXA, the Guacamole sign-in, Grafana's logo, and the
desktops' wallpaper. Malcolm and Wazuh keep their own look.

Passwords are in [step 9](#9-passwords). Malcolm and Wazuh use self-signed
certificates, so your browser warns once.

Useful to know:

- The ports stay the same when a VM restarts. `phenix-tunneler list` (in a
  second terminal) shows them, and `phenix-tunneler move <id> <port>` moves one
  that clashes with something on your computer.
- If you turned phenix login on, add `--username <you>` to `serve`, and run
  `phenix-tunneler activate <id>` once for each forward in `list`.
- **Any other port of any VM:** open the VM's tile in the phenix page, click
  the arrow ("create port forward"), choose a local port and the VM's port.
  The tunneler picks it up at once. This is how to use a real RDP client on
  the engineering workstation (`ews`, port 3389).
- **A VM's screen and keyboard:** click the VM's screenshot in the phenix page.
  Every Linux VM logs in as `root` with no password on its console. That is how
  you reach the desktops, the HMI kiosks, and a shell on any VM.

**Without the tunneler**, on the phenix host itself: each forward also listens
on a random host port. Look it up, every time, and use the row whose `dst port`
is the VM's port (for FUXA, 1881):

```shell
docker exec phenix phenix mm -n cherrymountain-ci cc tunnel list scada-fuxa    # then open http://<host>:<src port>
```

cm-viz answers only to the names `localhost` and `127.0.0.1`, so it works
through the tunneler or `ssh -L`, not by the host's address.

## 9. Passwords

The range's app makes new passwords for each experiment and keeps them, mode
0600, in one file on the phenix host:

```shell
sudo cat /phenix/experiments/<experiment>/cherrymountain/secrets.json
```

| For | User | Key in `secrets.json` |
|---|---|---|
| FUXA, to operate from your own browser (scada-fuxa, bas-fuxa). The operator stations sign in with it by themselves | `operator` | `fuxa_operator_password` |
| FUXA editor (scada-fuxa, bas-fuxa) | `admin` | `fuxa_password` |
| OpenPLC runtime on every PLC (`https://<plc>:8443`) | `admin` | `openplc_password` |
| Grafana, on the historian and on sim-ctl | `admin` | `grafana_password` |
| InfluxDB on the historian | `admin` | `influx_password` (API token: `influx_token`) |
| Guacamole administrator | `cmadmin` | `guacamole_password` |
| Vendor technician, Guacamole | `ridgeline-akim` | `vendor_guac_password`, and a one-time code from `vendor_totp_secret` |
| Vendor technician, on the EWS | `ridgeline-akim` | `vendor_ews_password` |
| Malcolm | `malcolm` | `malcolm_password` |
| Wazuh dashboard | `admin` | `wazuh_password` |

The passwords survive a stop and start, and a new `workflow apply`. Deleting
the experiment deletes them. A second technician, `ridgeline-jdiaz`, is in
`vendor.json` beside `secrets.json`.

If a Malcolm or Wazuh sign-in fails, read what the VM itself set, on its
console: `cat /var/lib/cm-malcolm/admin-password` on malcolm, and
`grep 'user admin' /var/lib/cm-wazuh/passwords.txt` on wazuh.

## 10. A tour: use each part

### Operate the plant (scada-fuxa)

Open `http://localhost:11881`. The views are **Cellar**, **Brewhouse**,
**Batch**, **Cellar points**, **Packaging**, **Utilities**, **Power**, and
**Cellar CO2** (`ci` has the cellar only). Open one directly with
`http://localhost:11881/home?viewName=Cellar`.

- **Look.** Fermenter temperatures on the Cellar view should move within a
  minute. Values that never change mean the simulation is not reaching the
  PLCs: see [Troubleshooting](#troubleshooting).
- **Change a value.** Without a login FUXA only shows. Click the account icon
  at the top right, or just try to write: FUXA opens its sign-in box. Sign in
  as `operator`. The sign-in lasts 12 hours. Every value you may write has a
  box beside it: type and press Enter. Type engineering values (`66.0` for
  66 °F); bits take `1` or `0`.
- **Edit the screens.** Only `admin` can open the editor (`/editor`).
- **Acknowledge an alarm.** Open the alarm list in the header. Every alarm
  except the lowest level stays until someone acknowledges it.

| To do this | Write | On view |
|---|---|---|
| Change a fermenter's temperature | Press **By hand** for that fermenter, then set `CEL.FV101.TT01.SP` and `TT02.SP`. **Profile on** gives it back to the PLC's fermentation profile | Cellar |
| Change the fermentation profile for every fermenter | `CEL.PROFILE.TC01.SP` (fermenting, 66), `TC02.SP` (rest, 70), `TC03.SP` (crash, 34), `KC01.SP` (rest minutes, 180) | Cellar points |
| Brew | the **Start** button ([Brew a batch](#brew-a-batch)) | Batch |
| Run one brewhouse phase by hand, only while the Batch view says Idle | `BH.BATCH.XC01.PHASE` (1 to 10), then `BH.BATCH.XC01.CMD` (1 start, 2 hold, 3 restart, 4 abort, 5 reset) | Brewhouse |
| Start a cleaning (CIP) circuit | `UTL.CIP.XC01.CIRCUIT` (1 to 7), then `UTL.CIP.XC01.CMD` (1 start, 2 hold, 0 resume, 3 abort) | Utilities |
| Start or stop the packaging line outside its shift | `PKG.LINE.PACKML.CMD` (1 reset, then 2 start; 3 stop) | Packaging |
| Keep the packaging line stopped during its shift | `PKG.LINE.PACKML.CMD` = 4 (hold), and 5 to let it go. A stop (3) lasts about two minutes: the line's operator starts the line again | Packaging |
| Mark a vessel entry permit | `CEL.ENTRY.HS01.ENTRY` to `HS06`, `BH.ENTRY.HS01.ENTRY`, `HS02`; check `XSnn.LOCKED` | Cellar points, Brewhouse |
| Reset the CO₂ supply shutoff after a gas alarm | `UTL.CO2.XC01.RESET` = 1 (accepted once the detection relay has been healthy 60 s; state is `UTL.CO2.XS02.SHUTOFF`) | Utilities |

The plant also runs on a clock. It starts on a Wednesday at 04:30 (simulated
local time), brew days are Monday and Wednesday at 05:00, the packaging shift
is 06:00 to 14:30 on weekdays, and the power PLC exercises the generator on
Wednesdays at 10:00. The cellar crew racks finished fermenters, keeps the
serving tank filled, and cleans emptied vessels as the plant needs it. Nobody
has to start the packaging line: its operator (a
part of the simulation) presses Reset and Start at the line's panel at 06:00
and Stop at 14:30, and starts the line again when it has stood stopped for two
minutes during the shift. Fermentation runs 48 times faster than real time, so a
two-week fermentation takes about seven hours; everything else is real time.

### Brew a batch

`lite` and `full`. scada-fuxa carries the **batch engine**: it takes the
brewhouse PLC through a brew, one phase after another, with the numbers of the
recipe (mill, heat the liquor, mash in, two mash steps, vorlauf, lauter, boil,
whirlpool, knockout to a fermenter, ready for cleaning). Open the **Batch**
view and sign in as `operator`.

| Button | What it does |
|---|---|
| **Start** | Adds one brew to the queue. It starts when the brewhouse PLC is idle, no brewhouse vessel has an entry permit, and a fermenter is empty |
| **Hold** | Pauses at the next safe point: the running phase stops its equipment and keeps the time it has served |
| **Restart** | Continues a held batch |
| **Abort** | Stops the phase, empties the queue, and ends the batch as Aborted |
| **Reset** | Acknowledges a Complete or Aborted batch, and clears the queue and the engine's alarms |

- **Status** says what the batch is doing, or why it waits. A start that is
  refused stays queued and begins when the cause is gone.
- **One brew takes five to six hours**; a brew day is two brews into one
  fermenter.
- **Brew days start by themselves.** At 05:00 on Mondays and Wednesdays
  (simulated time) the plant asks for two brews, as if someone pressed the
  request button on the brewhouse panel. So does the `start-batch` event
  ([Run an exercise](#run-an-exercise)). The first one comes 30 minutes after
  the range starts.
- **If scada-fuxa is down**, a request waits at the panel and is taken when
  FUXA is back. A FUXA restart in the middle of a batch picks the batch up
  where it was.
- **A phase that takes too long**, or a PLC that stops answering, holds the
  batch and lights an alarm on the view. Fix the cause, then Restart or Abort.
- **Leave the phase and command tags alone while a batch runs.** The engine
  holds the batch if the PLC does something it did not ask for.

### Building automation (bas-fuxa)

Open `http://localhost:11882`. The **Building** view shows the taproom rooftop
unit, the cold room, and ventilation with the CO₂ sensors. bas-fuxa reads three
BACnet controllers (`bas-rtu-taproom`, `bas-coldroom`, `bas-vent`). Signed in
as `operator`, you can command their outputs and setpoints. A command overrides
the controller's own logic until it is released; FUXA cannot release one, the
engineering workstation can (see below). scada-fuxa reads the same CO₂ values
through the firewall and never writes them.

### The operator stations (hmi-brewhouse, hmi-packaging)

Each is a desktop that opens one FUXA view full screen. See them in the phenix
page by clicking the VM's screenshot. They sign themselves in as `operator`
about a minute after scada-fuxa is up, so you can write values and press
buttons there with no login. They renew the sign-in by themselves about every
8 hours; the view reloads when they do.

- **Is it signed in?** The account icon at the top right is blue when it is,
  and black when it is not. On the station's console, `cat /run/cm-kiosk/status`
  says `state=operator`, or `state=guest` with the reason.
- **It only ever is the operator.** If you sign in there as another account,
  the station signs back in as `operator` within seconds. Use your own browser
  to work as `admin`.

### PLCs and field devices, from the engineering workstation

The engineering workstation `ews` (10.20.30.12) has the tools. Open its screen
from the phenix page (click its screenshot; you are `root`), or create a port
forward to its port 3389 and use an RDP client as a technician
([vendor access](#vendor-remote-access) has the account).

| Task | Command on the EWS |
|---|---|
| PLC status | `python3 -I /usr/local/lib/cm-ews/cm_plc_tool.py status --plc plc-cellar --host 10.20.10.12` (expect `STATUS:RUNNING`) |
| Read a Modbus value (fermenter 101 temperature; 6600 is 66.00 °F) | `mbpoll -m tcp -a 1 -0 -1 -t 3 -r 101 10.20.10.12` |
| Write a Modbus setpoint | `mbpoll -m tcp -a 1 -0 -1 -t 4 -r 106 10.20.10.12 6400` |
| Read an OPC UA value from plc-brewhouse (1680 is 168.0 °F) | `/opt/ews-venv/bin/uaread -u opc.tcp://10.20.10.11:4840/openplc/opcua -n "ns=2;s=BH.HLT.TT01.PV"` |
| Load a PLC program again | `python3 -I /usr/local/lib/cm-ews/cm_plc_tool.py upload --plc plc-cellar --host 10.20.10.12 --zip /opt/cherrymountain/ews/programs/plc-cellar.zip` (the PLC compiles for a few minutes) |
| Read and command a BACnet point | the BAC0 example in [phenix-injects/otsim/README.md](phenix-injects/otsim/README.md#check-it-on-a-running-range) |

| PLC | Address | Protocol to SCADA |
|---|---|---|
| plc-brewhouse | 10.20.10.11 | OPC UA, port 4840 |
| plc-cellar | 10.20.10.12 | Modbus TCP, port 502 |
| plc-packaging | 10.20.10.13 | S7, port 102 (read it through FUXA; the EWS has no S7 tool) |
| plc-utilities | 10.20.10.14 | Modbus TCP, port 502 |
| plc-power | 10.20.10.15 | Modbus TCP, port 502 |

OpenPLC has no web page of its own in this version, only an API at
`https://<plc>:8443/api` (user `admin`). A write from the EWS to a life-safety
point is exactly what the SOC's detections watch for, so expect an alert.

### The historian

Open `http://localhost:13000` (Grafana, no login needed to look). It graphs
what FUXA records to InfluxDB: process values, alarms, and the production
records a brewery keeps. InfluxDB itself is on the historian's port 8086 and
MQTT on 1883; create a port forward if you want them on your computer.

### Vendor remote access

`full` only. *Ridgeline Process Controls*, the integrator, reaches the plant
the way a real vendor would: a WireGuard tunnel from the internet side to
`dmz-vpn`, then the Guacamole jump host `dmz-jump`, then the EWS.

1. Open the **vendor-ws** screen from the phenix page.
2. Check the tunnel: `wg show wg0` should list a recent handshake. If it does
   not: `systemctl restart wg-quick@wg0`.
3. Get the technician's one-time code:
   `python3 -I /usr/local/lib/cm-desktop/cm_vendor_session.py --totp`. It
   prints the account and a code that is valid for a few seconds.
4. In the browser on vendor-ws, open `https://10.10.20.11/` and accept the
   certificate. Sign in as `ridgeline-akim` with `vendor_guac_password`, then
   the code.
5. Open the connection `ews-rdp-ridgeline-akim` (a desktop) or
   `ews-ssh-ridgeline-akim` (a shell). Guacamole logs in to the EWS for you and
   records the session on dmz-jump under `/var/lib/cm-guacamole/recordings`.
6. Work on the EWS as above. The technician's account can run `cm_plc_tool.py`.

`cmadmin` (with `guacamole_password`) administers Guacamole. Its first sign-in
shows a QR code to enroll a one-time-code app.

### The SOC: Malcolm and Wazuh

`full` only.

- **Malcolm**, `https://localhost:18443`, user `malcolm` with
  `malcolm_password`. Use `/dashboards/`
  for the Zeek and Suricata dashboards (look for Modbus, BACnet, DNP3, and
  OPC UA) and `/arkime/` for sessions and packets. Mirrored packets carry
  their VLAN tag, so packet filters need `vlan and ...`.
  - Is capture live? On the malcolm console run `cm-malcolm-health`. It should
    print `HEALTHY` with a packet count and a count of Zeek records.
  - `cm-malcolm-selftest` plays a test capture through Zeek and Suricata.
- **Wazuh**, `https://localhost:18444`, user `admin` with `wazuh_password`.
  The range's own rules
  are ids 100100 to 100413. In Threat Hunting, filter on
  `rule.groups: cm_vendor`, `cm_wireguard`, or `cm_firewall`. Agents appear in
  the groups `ops`, `hmi`, `bas`, `dmz`, and `soc`.
  - On the wazuh console, `cm-wazuh-health` checks services and agents, and
    `cm-wazuh-selftest` feeds sample log lines through the rules.
- **soc-analyst** is the analyst's desktop. It opens Malcolm and Wazuh in a
  browser by itself, once both are up.

### The white cell: see and drive the truth

`lite` and `full`. These show what is really happening in the plant, whatever
the operators and the SOC see. Do not show them to trainees.

- **cm-viz**, `http://localhost:18090`: a 3D view with three scenes, chosen by
  `?scene=cellar` (cellar CO₂ and life safety), `?scene=fermenters`, or
  `?scene=power`. `/svg.html` is the same without 3D graphics.
- **Event API**, `http://localhost:18080/api/v1/`: `health`, `kpis` (batches,
  bottles, kegs, energy, demand, compliance figures), `state` (every value),
  and `events` (what has been injected). [Run an exercise](#run-an-exercise)
  shows how to post events.
- **Ground truth**, `http://localhost:13001`: the dashboard "CherryMountain
  ground truth" graphs the simulation's own records and lists injected events.
  The records are in OpenSearch at `http://localhost:19200`, in indexes named
  `cherrymountain-truth-<part>-<date>`. Each record carries the wall-clock time
  and the simulated time, so you can line it up with Malcolm and Wazuh.

## Run an exercise

**Scripted runs** (`full`). SCORCH runs a prepared scenario and collects the
evidence under `/phenix/images/<experiment>/files/scorch/run-<N>/`, which is
also the Files tab of the experiment.

```shell
docker exec -it phenix phenix experiment scorch cherrymountain-brewery --run 2
```

| Run | Name | What it does |
|---:|---|---|
| 0 | `vendor-maintenance` | A scripted vendor session re-uploads a PLC program; checks that Wazuh and Malcolm saw it; pauses for the analyst (type `exit` to go on, so it needs `-it`) |
| 1 | `baseline-brew-day` | Requests a brew day and records its first half hour, with packet captures and exports |
| 2 | `utility-outage` | Opens the utility breaker for ten minutes; checks that the generator carries the emergency loads |
| 3 | `cellar-co2-leak` | Releases CO₂ at a fermenter; checks the purge fans and the alarm |
| 4 | `packaging-shift` | Jams packaging machines on a degraded field network |

A run blocks until it is done, 30 to 45 minutes for most.

**Events by hand** (`lite` and `full`). Post to the event API:

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

A good post answers 202; a bad one answers 400 and says why. The events are
`start-batch`, `utility-breaker`, `genset-exercise`, `chiller-fault`,
`co2-release`, `co2-leak`, `gas-detection`, `refrigerant-leak`, `bess-thermal`,
`jam`, `cip-panel`, and `weather-step`. [sim/README.md](sim/README.md) lists
every parameter.

Then compare what happened (cm-viz, ground truth) with what the trainees saw
(FUXA, Malcolm, Wazuh).

**Rules of play.** The simulation network (MGMT), the VM consoles, and the
phenix host are out of bounds for players. Anyone who can reach the phenix
host can reach every forward, the event API included, so keep it on a network
you trust.

## Stop, reset, and clean up

```shell
docker exec phenix phenix experiment stop cherrymountain-lite      # stop; exports FUXA history and an InfluxDB backup first
docker exec phenix phenix experiment start cherrymountain-lite     # start again with a fresh plant and the same passwords
docker exec phenix phenix experiment delete cherrymountain-lite    # remove it, passwords included
```

- A stop and start gives a fresh plant. phenix builds every VM's disk again
  from its image, so the simulation starts over, the PLCs load and compile
  their programs again, and everything stored inside a VM is gone: the
  historian's data, the batch engine's batch count, the Malcolm and Wazuh
  data, and Guacamole's recordings. The passwords stay. Export what you want
  to keep before you stop.
- The exports of a stop land in `/phenix/images/<experiment>/files/cherrymountain/`.
- After a `phenix experiment start` from the command line, the app's
  five-minute status refresh does not run. Start with `workflow apply` or from
  the phenix page, or refresh by hand:
  `docker exec phenix phenix experiment trigger running <experiment> cherrymountain`.
- The simulation ends after 7 days (24 hours in `ci`). Stop and start the
  experiment for a longer exercise.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `... is a Git LFS pointer; run git lfs pull` | Run `git -C /phenix/topologies lfs pull`, then apply again |
| No `/dev/kvm` | `kvm-ok`; on a cloud VM turn on nested virtualization |
| An image build fails | Read the end of `/phenix/images/<name>.log` for the URL or package that failed, and try again. The builds download from Ubuntu, GitHub, PyPI, npm, and vendor repositories |
| The VyOS build fails | It builds VyOS's rolling branch, which sometimes breaks for a day. Try later, and keep a `vyos.qc2` that works |
| `workflow apply` says a second experiment cannot use the bridge or VLANs | Another variant is running. Stop it first |
| `phenix-tunneler` lists nothing | The experiment is not running, or phenix is not the fork (the fork creates the forwards). `docker exec phenix phenix version` |
| A page does not answer on its local port | That VM's service is still starting. Open the VM's console and check it |
| SoH fails on a PLC | On the PLC's console: `journalctl -u cm-openplc-provision` shows the upload and the compile |
| Nothing moves in FUXA | `curl -s http://localhost:18080/api/v1/health`. If it says `starting`, a part of the simulation has not joined: on sim-ctl read `/var/log/helics-root-broker.log`, and on the device `journalctl -u ot-sim`. `nft list table inet cm_mgmt` on either shows whether the simulation network's firewall is dropping packets |
| bas-fuxa shows no values | On a `bas-*` console: `journalctl -u ot-sim \| grep bacnet-server` should say "first values from the message bus". [phenix-injects/otsim/README.md](phenix-injects/otsim/README.md) has more |
| FUXA does not take a value or a button | In your own browser: you are not signed in. Sign in as `operator` ([step 9](#9-passwords)). On an operator station: its own sign-in has not worked yet. `cat /run/cm-kiosk/status` on its console says why; it keeps trying, and the SoH check `kiosk-operator-session` shows the same |
| A brew does not start | Read **Status** on the Batch view. `start refused` names the cause: the brewhouse PLC is not idle (press **Reset**), a vessel has an entry permit, no fermenter is empty, or there is no link to the PLC. If the view shows nothing at all, on the scada-fuxa console: `journalctl -u fuxa \| grep cmBatchEngine` |
| Malcolm shows no traffic | Run `cm-malcolm-health` on its console. If the app's status does not say `"dfDefault": "set"` ([step 7](#7-wait-then-check-health)), set the option by hand: `docker exec minimega sh -c 'for p in $(ovs-vsctl --bare --columns=name find interface type=gre); do ovs-vsctl set interface "$p" options:df_default=false; done'` |
| SCORCH run 0 fails at `analyst-review` | Run it with `docker exec -it` |

## Known limits

Be ready for these. [DESIGN.md §19](DESIGN.md#19-risks-and-open-questions) has
the full list.

- **Nothing has run on VMs.** The first boot of each image is the first real
  test of its provisioning scripts. Parts were run for real outside a VM: the
  batch engine, its view, and the operator sign-in in a FUXA server; the
  BACnet controllers against BACnet clients; every PLC program through the
  compiler. The first things to check on a real range are that FUXA may write
  to the brewhouse PLC over OPC UA, and that Wazuh takes the `admin` password
  from `secrets.json`.
- **A crash sets off the fermenter's deviation alarms.** The cellar PLC takes
  each fermenter through its profile (66 °F fermenting, 70 °F for a three-hour
  rest, then 34 °F), and the cellar crew racks it once it is cold. While the
  beer cools, for about three hours, the fermenter's two temperature deviation
  alarms are on. They clear by themselves.
- **The bottling line waits for beer.** The filler holds when BBT-202 runs dry
  and starts again when the crew has refilled it. The line empties a tank in
  about 3.5 hours and two brew days make two tanks a week, so expect it to
  stand held for much of the week.
- **The batch engine's time limits are estimates.** If a healthy phase is held
  for "phase timed out", press Restart, and tell us which phase.

## Where to go next

- [DESIGN.md §15.9](DESIGN.md#159-operations): the operations runbook, failure
  modes, and the range's own security.
- [§10](DESIGN.md#10-network-architecture): zones, addresses, and what may talk
  to what. [§14](DESIGN.md#14-blue-team-monitoring): the detections.
- [§15.6](DESIGN.md#156-scorch-pipelines): the SCORCH runs.
  [§15.10](DESIGN.md#1510-white-cell-3d-view-cm-viz): the 3D view.
- [sim/README.md](sim/README.md): the simulation and every event.
- [apps/README.md](apps/README.md): the range's phenix app and its status.
- After a change to `plant/plant.yaml`: "Regenerate and test" in
  [README.md](README.md#regenerate-and-test).
