# Spike: host access via `phenix/startup-autotunnel`

**Question.** Can the host (and white-cell tooling on it) reach the in-range
services (FUXA, Grafana, event API, OpenSearch, Malcolm, Wazuh) through
`phenix/startup-autotunnel`, with the unique port plan in DESIGN §15.3 and no
tap or management NIC? **Decides:** the annotation list and boot-order notes.

## What the phenix source says (startup app, `main`)

- Each annotation entry is `sport`, `sport:dport`, or `sport:dhost:dport`
  (first number is the host listening port). Matches DESIGN §15.3.
- Tunnels are created at **PostStart**, once, in a goroutine. Except for
  `dport` of `VNC`, a tunnel is created only if the VM's `miniccc` C2 client
  is **already active** at that moment. If the VM boots later, the tunnel is
  silently skipped (TODO(verify): whether a re-trigger exists, such as a
  `phenix vm` tunnel command or restart). This is the main risk to test.

## Prerequisites

- phenix host with the `startup` app enabled (default apps) and port range
  11881-19200 free. Guests with `miniccc` in the image.

## Steps

1. `./check_ports.sh before` on the host (set `PLAN` to your list).
2. Apply `topology.yml` as an experiment and start it. On each guest, once
   booted, run `./serve_stubs.sh <ports>` (use the dport values: 3000 8080
   9200 on sim-ctl; 1881 on scada-fuxa) or bake it into an inject.
3. `./check_ports.sh after` on the host. Record which ports answer.
4. Repeat with stubs started before and after miniccc connects, and with the
   guest started late (`vm kill` then `vm start` after the experiment is
   up), to learn whether a late VM gets its tunnel.
5. Restart one tunnelled VM; check whether the tunnel survives.
6. From the host, run a real client, e.g. `curl -s localhost:19200` and
   `curl -sI localhost:11881`, and one write path (`curl -XPOST` to a stub
   that accepts it) to confirm the event-API use case.
7. Confirm 3000 (phenix UI) is unaffected.

## Pass / fail

- [ ] All planned ports answer within 2 minutes of experiment start when
      guests have miniccc running.
- [ ] No host port collision with phenix or Docker (`before` clean).
- [ ] A late-booting VM gets its tunnel (or there is a documented, scripted
      way to create it; record the command).
- [ ] POST/GET to the sim-ctl event API stand-in works from the host.

First three fail with no workaround: **no-go**; fall back to a dev-only `tap`
with `externalAccess` (DESIGN §15.2) and say so in §15.3.

## Result

- **Decision:** **go with two rules: discover the host port, and re-create
  forwards after a VM restart or a late boot.** The unique "sport" plan of
  DESIGN §15.3 does not work as written: the annotation's first number is not
  the port the host listens on.
- **Evidence** (run [42518460185](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42518460185), job "Spike autotunnel", artifact
  `evidence-autotunnel`; phenix `984869d` from GhostofGoes/sceptre-phenix
  `ci-e2e-minimega`, minimega 3.3.0; the experiment was started with the REST
  call `POST /experiments/{exp}/start` against a running `phenix ui`, the only
  way autotunnels are created, not with the CLI):
  - Guests registered their C2 client about 50 to 70 s after start. Every
    annotated tunnel then existed: `cc tunnel list <vm>` showed one per
    annotation and the stub banners came back through them (scada-fuxa:1881,
    sim-ctl:3000, :8080, :9200), and a POST to the sim-ctl event API stand-in
    worked from the host.
  - **Host ports are random.** The tunnels listened on 55421, 63542, 53917 and
    54020 (127.0.0.1, range 50000-64999 from `forwardPortBase` in
    `web/forward/forward.go`), not on the annotated 11881, 13001, 18080, 19200;
    nothing answered on those. The annotation's first number is only the key of
    the forward. Find the host port with `cc tunnel list <vm>` (row `dst
    127.0.0.1`, `dst port <guest port>`, column `src port`) or
    `GET /experiments/{exp}/vms/{vm}/forwards`.
  - phenix ui kept answering (HTTP 200) with the tunnels open.
  - **Restart:** after `vm kill` the tunnel was gone, and after `vm start` and
    C2 re-registration it was not re-created (no tunnel in `cc tunnel list`).
    `POST /experiments/{exp}/vms/sim-ctl/forwards?src=13002&host=127.0.0.1&dst=3000`
    (HTTP 204) created a working tunnel (new random host port).
  - **Late VM (case: C2 later than 5 minutes):** late-vm was paused before its
    C2 client could register. phenix ui logged `startup autotunnel not created:
    waiting for the C2 client` after the 5 minute C2 wait and made no tunnel.
    After the VM was resumed and registered (state `PAUSED` before, C2 up
    afterwards), still no autotunnel (`late_vm_gets_autotunnel False`); the REST
    forward call (204) gave a working tunnel. The other case (C2 within 5
    minutes) is what happened to the other guests: the tunnel appears once C2
    is up, there is no need to start the VM after phenix.
  - The behavior read from the fork's `app/startup.go`: autotunnels are
    published only to a running `phenix ui` (a CLI `experiment start` logs
    `autotunnels are created only for experiments started through phenix ui`
    and makes none), each waits for the VM's C2 client for up to
    `DefaultC2Timeout` (5 min, poll every 2 s) and then gives up with no later
    retry, and `VNC` tunnels skip the wait. Upstream sandialabs `main`, as the
    coordinator reports it (not re-checked here), instead makes one
    `IsC2ClientActive` check per tunnel and drops it silently on failure, so
    on upstream a VM that boots after PostStart gets no tunnel and no log line.
- **Follow-ups:**
  - Rewrite DESIGN §15.3 and the plantevent/SOCS tooling that assume
    `localhost:19200` and the like: tools must look up the port (the cherrymountain
    app or SCORCH can run `cc tunnel list` or call the forwards API) or the
    design needs fixed ports from another mechanism (for example a forward
    created through the REST call with a chosen `src` that a local tunneler
    listens on, or a host-side `socat` to the discovered port).
  - Start the range through `phenix ui` (`workflow apply` against the UI API),
    never through a separate CLI process.
  - Add a SoH step or a scenario hook that re-creates forwards after any VM
    restart, and note that a VM whose C2 takes over 5 minutes needs it too.
  - The late-VM case uses a pause, not a kill: a VM killed three seconds into
    boot did not boot again in earlier runs.
