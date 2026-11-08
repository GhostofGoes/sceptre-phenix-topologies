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

- **Decision:** go / go with boot-order rule / no-go
- **Evidence:** (check_ports output, late-boot behavior)
- **Follow-ups:** (how to re-create tunnels, whether SoH should wait for C2)
