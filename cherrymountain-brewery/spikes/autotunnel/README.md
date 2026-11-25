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

- **Decision:** **go.** With the phenix fork at `fa8338d` every annotated
  tunnel appears by itself, including for a VM whose C2 client is more than 5
  minutes late, and tunnels come back by themselves after `vm kill` / `vm
  start` and after a guest reboot. One change to DESIGN §15.3 is needed: the
  annotation's first number is **not** the port the host listens on.
- **Evidence** (run [42523133094](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42523133094), job "Spike autotunnel", artifact
  `evidence-autotunnel`, phenix `fa8338d5bd90c5ac7bd5a46f77a5eaa880d7e4eb` of
  GhostofGoes/sceptre-phenix `ci-e2e-minimega`, minimega 3.3.0; all checks
  PASS. The experiment was started with `POST /experiments/{exp}/start`
  against a running `phenix ui`. The final run, [42534059315](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42534059315), passed all checks again on phenix `041b001` (the rebased fork head, same autotunnel loop):
  - Guests registered their C2 client 47 to 70 s after start; then every
    annotated tunnel existed in `cc tunnel list <vm>` and the stub banners came
    back through them (scada-fuxa:1881, sim-ctl:3000, :8080, :9200), and a POST
    to the sim-ctl event API stand-in worked from the host. phenix ui kept
    answering.
  - **Host ports are random.** The tunnels listened on, for example, 52424,
    57571, 64247 and 53502 (range 50000-64999, `forwardPortBase` in
    `web/forward/forward.go`), not on the annotated 11881, 13001, 18080, 19200
    (nothing answered there). The first number is only the key of the forward.
    Find the host port with `cc tunnel list <vm>` (row `dst 127.0.0.1`,
    `dst port <guest port>`, column `src port`) or `GET
    /experiments/{exp}/vms/{vm}/forwards`.
  - **`vm kill` then `vm start` of sim-ctl:** the tunnel disappeared and, once C2
    came back, phenix re-created it by itself (new random port, banner read).
  - **Guest reboot of scada-fuxa** (`reboot` run through cc): the tunnel was
    re-created by itself (new port 61480 against 52424 before), even though
    the C2 entry never dropped from `cc clients` during the reboot.
  - **Late VM:** late-vm was paused (`vm stop`) before its C2 client could
    register and stayed paused for 310 s, longer than the old 5 minute limit. It
    had no tunnel while paused; phenix ui logged `startup autotunnel waiting
    for the C2 client` and later `still waiting ... the VM may not be started
    yet or may boot slowly` (no give-up). After `vm start` and C2 registration
    the tunnel appeared with no help (host port 58540, banner read).
  - Log lines of the fork's loop: `startup autotunnel requested`, `waiting for
    the C2 client` (with `waited=`), `still waiting`; all in the artifact's
    `phenix-ui-autotunnel-lines.txt`.
- **Earlier result, kept for the record** (run [42518460185](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42518460185), phenix
  `984869d`): that version gave up after 5 minutes (`startup autotunnel not
  created: waiting for the C2 client`) and never re-created a tunnel after a
  restart or late boot; only `POST .../forwards?src=..&host=127.0.0.1&dst=..`
  (HTTP 204) did. Upstream sandialabs `main`, as the coordinator reports it
  (not re-checked here), makes a single `IsC2ClientActive` check per tunnel and
  drops it silently on failure. If the project runs a phenix without the fork's
  loop, keep the REST forward call as the fallback.
- **Follow-ups:**
  - Rewrite DESIGN §15.3, and `plantevent`/SCORCH tooling that assumes
    `localhost:19200`: tools must look the port up (`cc tunnel list` or the
    forwards API), or the design needs fixed host ports from another mechanism
    (a host-side `socat` to the discovered port, for example).
  - Requires the fork's autotunnel loop (or the REST fallback) in whatever
    phenix the range runs on; plain upstream does neither re-creation nor a
    log line.
  - `vm restart` through the UI was not tested (the kill/start pair above is
    the same C2 event).

### Round 2: an experiment started by the phenix CLI

- **Decision:** **go.** The tunnels of an experiment started with `phenix
  experiment start` (its own process, which exited after 1 s) appear without
  help while a separate `phenix ui` runs, carry data, and survive a `phenix ui`
  restart. The fork's loop reads the running experiments from the store, so
  nothing depends on which process started the experiment.
- **Evidence** (run [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499), job "Spike autotunnel", artifact
  `evidence-autotunnel`, directory `autotunnel-cli`; phenix `7b59270` of the
  fork, minimega 3.3.0; all checks PASS, including the REST case of round 1
  again):
  - `topology-cli.yml` (sim-ctl and scada-fuxa, another name) was created, and
    `phenix experiment start` returned after 1 s; the C2 clients registered
    70 s after the start.
  - phenix ui logged `startup autotunnel requested` for all four entries of
    experiment `spike-autotunnel-cli` 3 s after the start (2026-11-09
    00:52:36), then `waiting for the C2 client`, then created them. The stubs
    answered through all four tunnels (host ports 58980, 54623, 64216, 50352),
    and `GET /experiments/spike-autotunnel-cli/vms/<vm>/forwards` listed them
    with owner `bot`.
  - After `phenix ui` was stopped and started again, it logged `requested` for
    the four entries again and the tunnels (minimega's, so they never went
    away) still carried data; the forwards API listed them.
- **Follow-ups:** none new. The DESIGN §15.3 statement that a CLI-started
  experiment works only with the fork's loop stands; this run used the fork.
