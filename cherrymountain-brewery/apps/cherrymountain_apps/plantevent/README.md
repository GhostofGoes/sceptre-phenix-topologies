# plantevent Component

SCORCH component that injects operational events into the CherryMountain
Brewery simulation through the sim-ctl event API
([DESIGN.md 9.5](../../../DESIGN.md#95-scenario-events-and-ground-truth),
[15.6](../../../DESIGN.md#156-scorch-pipelines)).

```
type:   plantevent
exe:    phenix-scorch-component-plantevent
stages: configure, start, stop, cleanup
```

Every stage it is listed in does the same thing: check the API's health, then
post each entry of `events`, in order. For events at different points of a
run, define one `plantevent` component per point (see the examples).

## What you get

In the component's run folder
(`<files>/scorch/run-<run index>/<component name>/loop-<loop>-count-<count>/`):

```
plantevent_<stage>.json     each event posted: time, request body, and the API's reply
<stage>-<UTC time>.json     the SCORCH status file (logs, and the error on failure)
```

## Before you start

- sim-ctl runs the event API on port 8080 (`cherrymountain_sim`), and its
  topology node carries the autotunnel entry `"18080:8080"`
  (`phenix/startup-autotunnel`).
- **The host port is not 18080.** minimega listens on a random port in
  50000-64999 and picks a new one whenever the tunnel is re-created. The
  component looks the port up at the start of every stage with
  `cc tunnel list sim-ctl` (the row with `dst 127.0.0.1` and `dst port 8080`)
  and connects to it on the minimega host that serves sim-ctl (127.0.0.1 on the
  headnode). It never assumes a fixed port.
- phenix's `GET /experiments/{exp}/vms/{vm}/forwards` cannot replace that
  lookup: its `srcPort` is the annotation label, and the host port is not in
  the reply.
- With phenix's autotunnel loop (the fork's `ci-e2e-minimega`), the tunnel is
  always there. Upstream phenix creates none for a CLI-started experiment and
  never re-creates one; with `create_tunnel: true` (the default) the
  component then opens one itself, the same way phenix does
  (`cc tunnel sim-ctl <random port> 127.0.0.1 8080`), and later stages reuse it.

## Metadata

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `sim_ctl.hostname` | hostname | no | `sim-ctl` | VM that runs the event API |
| `sim_ctl.port` | port | no | `8080` | The API's port inside the VM |
| `sim_ctl.url` | URL | no | | Send to this URL instead of looking up the tunnel (for example a host-side relay) |
| `sim_ctl.create_tunnel` | bool | no | `true` | Open a cc tunnel when none exists |
| `sim_ctl.timeout` | seconds | no | `10` | Time to wait for each request |
| `events` | list | yes | | Events to post, in order. At least one |
| `interval` | seconds | no | `0` | Pause between two events |
| `check_health` | bool | no | `true` | `GET /api/v1/health` before the first event |

Events (`event` selects the kind):

| `event` | Fields | Meaning |
|---|---|---|
| `start-batch` | `recipe` (default `fire-of-the-phenix`), `batch_id` | Start a brew |
| `utility-breaker` | `action`: `open` or `close` (required) | Utility outage and restoration |
| `chiller-fault` | `compressor` (1-8, default 1), `action`: `fail` (default) or `clear` | Glycol chiller compressor fault |
| `co2-release` | `fv` (`FV-102`, `BBT-201`; required), `rate`: `low`, `medium`, `high` (default), `duration` (default 600 s) | CO2 release into the cellar |
| `jam` | `machine` (`depalletizer`, `rinser`, `filler`, `capper`, `labeler` (default), `packer`), `duration` (default 300 s) | Packaging jam |
| `weather-step` | `delta_f` (-60 to 60, required), `duration` (default 0: hold) | Step the outside temperature |

Each event is posted as JSON to `POST /api/v1/events`, with the fields above
as keys (for example `{"event": "co2-release", "fv": "FV-102", "rate":
"high", "duration": 600.0}`).

## Examples

A brew day: start a batch when the run starts.

```yaml
components:
  - name: start-brew
    type: plantevent
    metadata:
      events:
        - event: start-batch
          recipe: fire-of-the-phenix
runs:
  - configure: []
    start: [start-brew]
    stop: []
    cleanup: []
```

A utility outage: open the breaker at the start, close it at the stop.

```yaml
components:
  - name: outage-open
    type: plantevent
    metadata:
      events:
        - event: utility-breaker
          action: open
  - name: outage-close
    type: plantevent
    metadata:
      events:
        - event: utility-breaker
          action: close
runs:
  - configure: []
    start: [outage-open]
    stop: [outage-close]
    cleanup: []
```

A cellar CO2 leak during a heat wave, through a fixed relay instead of the
tunnel lookup.

```yaml
components:
  - name: co2-leak
    type: plantevent
    metadata:
      sim_ctl:
        url: http://127.0.0.1:18080
        timeout: 5
      interval: 30
      events:
        - event: weather-step
          delta_f: 15
        - event: co2-release
          fv: FV-102
          rate: high
          duration: 900
runs:
  - configure: []
    start: [co2-leak]
    stop: []
    cleanup: []
```

To check a config before an experiment: `phenix-scorch-validate scenario.yaml`
(it checks the SCORCH structure; the event fields are checked when the stage
runs).

## Troubleshooting

| Error | Cause and fix |
|---|---|
| `no cc tunnel to sim-ctl:8080; is the VM's C2 client active, and does sim-ctl carry a phenix/startup-autotunnel entry for port 8080?` | `create_tunnel: false` and no tunnel exists. Wait for sim-ctl's C2 client, add the autotunnel entry, or allow the component to create the tunnel. |
| `sim-ctl has no active C2 client, so no cc tunnel can reach it` | sim-ctl has not booted or miniccc is not running. Check `cc clients`. |
| `GET /api/v1/health: ConnectionError` | The tunnel exists but nothing listens on port 8080 in sim-ctl. Check the event API service there. |
| `POST /api/v1/events: HTTP 400` | The API refused the event. Its reply is in the SCORCH status file's logs. |
| `events.0: Input tag 'outage' found using 'event' does not match any of the expected tags` | Unknown event kind. Use one from the table above. |
| warning `metadata warning for component '<name>': events.0.fvv: unknown key, ignored` | A misspelled field; it is not sent. Fix the key. |

## Limitations

- The event API is part of `cherrymountain_sim` and is not implemented yet;
  the ci variant's sim-ctl does not serve it. The contract above is what this
  component sends.
- A tunnel the component creates stays open until sim-ctl's C2 client goes.
- Events are posted once; there is no retry. Use SCORCH `loop` with `replace`
  to repeat or randomize them.
