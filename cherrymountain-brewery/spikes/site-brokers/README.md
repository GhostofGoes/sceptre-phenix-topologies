# Spike: one HELICS broker for each site

Question: The fleet is four breweries in one phenix experiment, and each site
simulates its own plant. Can each site run its own HELICS federation, with its
own broker on its own MGMT network, and with today's federate and topic names?
Does one site restart alone? What keeps two sites apart when their brokers can
reach each other? What must change in the range?

A spike is a small test that answers a design question. This spike answers the
question that section 4.7 of [PLANS.md](../../docs/multisite/PLANS.md) leaves
open for stage 3.

The kit uses these terms:

- HELICS is the co-simulation framework of the range. A federate is one
  simulation program. A federation is a group of federates that run together.
- A broker is the process that joins the federates of one federation and
  keeps their time. A core is the part of a federate that talks to the broker.
- A topic is the name of a published value, for example
  `cellar/fv101-side.temp_f`. An endpoint is a named mailbox for messages, for
  example `cellar/updates`.
- ot-sim is the simulator of the field devices. Its I/O module is a federate.
- A network namespace is a separate network stack in the Linux kernel.
- A kit is the set of files that runs one spike.

## What it builds

`site_brokers.py` builds the lab from network namespaces on one machine. No VM
runs.

| Part of the range | In the lab |
|---|---|
| A site's MGMT VLAN | Two namespaces and one virtual link between them. No link joins two sites |
| `sim-ctl` | The namespace `cmsb-<site>-ctl`, with the broker and the weather federate. Its address is 172.16.10.1 at `fr`, and the site's number moves the second octet |
| The other simulation hosts and the field devices | The namespace `cmsb-<site>-sim`, with the address of `sim-process` |
| The broker | `helics_broker`, with the command line that the phenix `helics` app writes for a root broker |
| The federates | The real `cherrymountain_sim` package, with the command line of the unit `cm-federate@.service` |
| A field device | A stand-in: one HELICS federate in Python. It has the device's name, subscriptions, and endpoint from `helics.json` |

Each site gets its own inputs, so that its values differ from the values of
the other sites. The inputs are the seeds, the start temperatures of the
fermenters, the elevation, and the first day of the calendar. The kit changes
no federate name, no topic, and no endpoint.

The kit has four parts:

| Part | What runs at each site | Source of `helics.json` |
|---|---|---|
| `ci` | The real `cellar` federate and one stand-in, at four sites | `plant/generated/ci/` |
| `full` | 7 real federates and 12 stand-ins, at four sites. All 19 names are the same at every site | `plant/generated/full/` |
| `fleet` | 19, 12, 19, and 14 federates at `fr`, `fc`, `ep`, and `cs`. A field device's federate carries the site's prefix | `plant/generated/fleet/<site>/` |
| `shared` | The `ci` federation of two sites, in one namespace that holds both broker addresses | `plant/generated/ci/` |

Two studies run next to the parts, each on a network of its own. In the first,
only the federates of a site restart. In the second, only its broker restarts.
There, each federate runs as a small supervisor that starts its process again
5 s after an exit, as the unit does.

## Prerequisites

- Linux, root, and iproute2 (`ip netns` and `ss`). The kernel needs veth and
  bridge devices.
- Python 3.12 with `helics` 3.6.1, `pydantic`, `simpy`, and `opendssdirect.py`
  0.8.4. The kit takes the `sim/` package from this repository.
- For the `fleet` part: the files under `plant/generated/fleet/`. Without
  them, the kit reports that the part did not run.

## Steps

```shell
python3.12 -m venv ~/brokers-venv
~/brokers-venv/bin/pip install 'helics==3.6.1' 'pydantic>=2.7' 'simpy>=4.1' 'opendssdirect.py~=0.8.4'
cd cherrymountain-brewery/spikes/site-brokers
sudo -E ~/brokers-venv/bin/python site_brokers.py
```

The script prints PASS or FAIL for each check, and its exit status is 0 only
when every check passes. It writes `summary.md` and `result.json` under
`$CM_EVIDENCE_DIR/site-brokers` (the default is `evidence/`). At the end, it
removes every namespace and process that it made. A run takes about 6 minutes.

- `--only ci` runs one part (`ci`, `full`, `fleet`, or `shared`).
- `--sites fr,ep` runs fewer sites.
- `--down` removes the namespaces of a run that did not clean up.
- `--keep-work` keeps the logs and the site files of the run.

The kit proves separation in three ways:

1. Reference: In the `ci` part, the cellar takes no input from another
   federate. The kit steps the same model with no HELICS. Each received value
   must equal that reference exactly.
2. Marks: In the `full` and `fleet` parts, each site's elevation sets its air
   pressure. The weather federate hands the pressure to the cellar. The cellar
   publishes a setpoint that is 1 psi above it. A stand-in must receive its
   own site's setpoint in every step. A stand-in must also receive its own
   site's calendar day from the power federate.
3. Cause: The kit sends a write or a scenario event at one site. The effect
   must show at that site and at no other.

## Pass / fail

- [x] Four federations with the same names run at once, each with its own
      broker, in the `ci`, `full`, and `fleet` parts.
- [x] Each federation receives the values of its own site only.
- [x] A write to `cellar/updates` and a scenario event change the site that
      got them, and no other.
- [x] One site stops and starts its broker and federates. The other sites
      miss no step, and their values stay equal to the reference.
- [x] On one network, a broker address for each site keeps two federations
      apart. A port for each site does the same.

## Result

- Decision: go. Each site can run its own federation, with its own broker and
  with today's names. Two conditions apply. First, the network must separate
  the sites, because a broker key alone does not protect a federation. Second,
  a site restarts as a whole: its broker and every federate.
- Run: The kit ran on `feat-cherrymountain-multisite`, in a container with 4
  CPUs and no VM. The versions were Python 3.12.3, helics 3.6.1 from pip, and
  kernel 6.18. All 16 checks passed in 328 s.
  [`evidence/site-brokers/summary.md`](evidence/site-brokers/summary.md) has
  the full record.

### Same names, separate values

| Part | Federates in all | Result |
|---|---:|---|
| `ci` | 8 | Each root broker lists `cellar` and `rio-cellar`. The stand-ins received 4,686 values. All equal their own site's reference, and no step equals another site's reference |
| `full` | 76 | Each root broker lists the same 19 names. Each stand-in received its own site's setpoint and calendar day in every step |
| `fleet` | 64 | The five federates that every site has keep their names (`brewhouse`, `building`, `cellar`, `utilities`, `weather`). The marks arrived as in the `full` part |

The marks of the `full` part, as the stand-ins received them:

| Site | Elevation (m) | Setpoint of FV-102 (psia) | Calendar day |
|---|---:|---:|---:|
| `fr` | 1,585 | 13.136430 | 20,649 |
| `fc` | 1,525 | 13.226234 | 20,650 |
| `ep` | 1,300 | 13.567801 | 20,651 |
| `cs` | 1,839 | 12.762159 | 20,652 |

- From the namespace of one site, a TCP connection to the broker of another
  site fails with "Network is unreachable". Each broker listens on port 23404
  in its own namespace.
- A write to `cellar/updates` at `fr` (17.25) and at `cs` (18.5) changed the
  setpoint of FV-101 there. At `fc` and `ep` it stayed at 13.096.
- A CO2 release went to FV-102 at `fr` and to FV-103 at `cs`, through each
  site's own event API. Each fermenter leaked 40 g/s at its own site. The
  other fermenter, and both fermenters at `fc` and `ep`, leaked 0 g/s.
- The federates logged no warning and no error. That includes `fc`, which has
  no packaging and no power federate, and `cs`, which has no power federate.

### One site restarts alone

The kit stopped every process of `ep` with SIGTERM, waited 6 s, and started
the broker and the federates again.

| Part | Window (s) | Steps at each other site | Longest gap between two steps (s) | `ep` runs again |
|---|---:|---:|---:|---|
| `ci` | 20.0 | 20, with none missed | 1.00 | 9.0 s after its start, and its time starts at 1 |
| `full` | 20.9 | 21, with none missed | 1.02 | 9.8 s after its start |
| `fleet` | 21.4 | 21 or 22, with none missed | 1.16 | 10.3 s after its start |

- In the `ci` part, the other sites' values stayed equal to their reference
  through the restart.
- In the `full` and `fleet` parts, the event API of each other site reported
  `ok`, and its time went on. The time of `ep` started again, on `ep`'s own
  date.

A site does not restart in parts. Both studies watched for 261 s:

| What restarted | What happened |
|---|---|
| The federates, with the broker left up | The broker refused every start with "already in initialization mode". `cellar` started 43 times. The federation did not run again |
| The broker, with the federates left up | The cellar federate did not register at the new broker. It logged 258 lines with `forced Granted Time` and stepped on alone. The federation did not run again |

The log lines of the second row point to the real-time flag of the federates.
With that flag, HELICS grants a late step with no answer from the broker. The
kit did not test a federate without the flag.

### Two sites on one network

The `shared` part puts the broker addresses of `fr` and `ep` in one namespace.

| Case | What HELICS did |
|---|---|
| A second broker with the `helics` app's command line | It did not get port 23405, and it exited with code 252 after 4.6 s. The app starts the broker in the background, so nothing reports the exit |
| A field device of `ep` that reaches the broker of `fr` while that broker waits | It joined. It received 165 values, and all were `fr`'s. No message named the wrong site |
| A federate that arrives when the federation runs | The broker refused it with "already in initialization mode". Then the broker dropped the first core that had registered, and `fr`'s cellar federate exited |
| A broker key (`--brokerkey`) | The broker refused a federate that had `ep`'s key ("broker key does not match"). The same refusal also dropped `fr`'s cellar federate, which had the right key |
| A broker name (`--brokername` at the federate) | No effect. A federate that named `ep-sim-ctl` joined `fr-sim-ctl` |
| `--local_interface` with the site's own address at each broker | Pass. Both brokers listened on port 23404, each on its own address, and each federation received its own values only |
| `--port` at each broker, and `HELICS_BROKER_PORT` for its federates | Pass. Both federations ran on one address and received their own values only |

- A broker with no interface option listens on 127.0.0.1 only. The `helics`
  app adds `--ipv4`, and then the broker listens on every address of its host.
- The real federate takes `HELICS_BROKER_KEY` and `HELICS_BROKER_PORT` from
  its environment. `federate.py` needs no change for either.
- The key is a second line of defense. It turns a silent join into a refusal
  with a clear message. It does not stop the harm of that refusal.
- `helics_broker` of the pip wheel is a Python script that runs the broker as
  a child. A SIGTERM to the script alone left the broker up on its ports. A
  signal to the process group stopped both, and systemd signals a unit that
  way.

### What the range must change

The line numbers below are for sceptre-phenix-apps at `f2c3757`, under
`src/python/phenix_apps/apps/`.

What the three apps do today:

- `helics` app: `broker.root` is one value, and the app stops without it
  (`helics/app.py` lines 18 to 22). A federate whose broker is not the root
  gets a sub-broker that joins the root (lines 92 to 98 and 145 to 154). Four
  sites then become one federation, and the equal names collide. The root
  broker's command is `templates/broker.mako` lines 10 to 17, and it ends in
  `--autorestart &`.
- `ot-sim` app: A device takes its broker from its own `helics.broker`, or
  from the app's (`otsim/app.py` lines 96 to 113). With `base-fed-count`, the
  app also starts a broker on that host (lines 37 to 59 and 400 to 413). That
  broker has `--web` and `--http_port=8080` (`templates/helics_broker.mako`
  line 2). In the lab, it took port 8080 on every address. The event API of
  `sim-ctl` binds 127.0.0.1:8080, and a bind of that address then failed.
- `cherrymountain` app: It starts no broker. It knows one `sim-ctl`
  (`metadata.py` lines 81 to 84 and 289) and one directory of generated files
  (`sources.py` line 109). `render_sim` copies `helics.json` and writes
  `HELICS_BROKER_ADDRESS` for each federate (`render.py` lines 748 to 780).
  The unit passes it on as `--broker-address`
  (`templates/systemd/cm-federate@.service` line 18).

The generated files: `plant/generated/<variant>/helics.json` has the broker
address, the federates, and `federate_count`. `phenix/helics.yaml` has the
root, and `phenix/ot-sim.yaml` has one `helics.broker.hostname` for the app
(line 1011 in `full`). `plant/generated/fleet/<site>/` has all three for each
site, with `<site>-sim-ctl` as the broker. One `helics` app block cannot hold
four roots.

The smallest set of changes:

| # | Repository | Where | Change |
|---|---|---|---|
| 1 | sceptre-phenix-apps | `cherrymountain/render.py` (`render_sim`), and a new unit in `templates/systemd/` | Render a broker unit on every host of type `sim-ctl`. The command is `helics_broker --name <host> -f<federate_count> --ipv4 --local_interface <MGMT address>`, with `Restart=always`. The count is in the site's `helics.json` |
| 2 | sceptre-phenix-apps | `cherrymountain/render.py` lines 696, 748, 1483, 1911, and 1916. `sources.py` line 109. `app.py` lines 447 to 452 | Replace the one `sim-ctl` and the one variant directory with those of the host's site. The topology files give each host a `site` label. The MGMT policy gets its hub and its peers for each site |
| 3 | sceptre-phenix-topologies | plantgen (`helics_app` in `render.py`) and `sync.py` | Write no `helics` app block into the fleet scenario. `sync.py` lines 306 to 317 accept a scenario without it |
| 4 | sceptre-phenix-topologies | plantgen (`otsim_app` in `render.py`, line 635) | Give each device of the merged `ot-sim` block its own `helics.broker.hostname`, which is `<site>-sim-ctl\|mgmt`. Set no `base-fed-count` |
| 5 | sceptre-phenix-topologies | The user guide, or a script | A restart of a site: stop its federate units, restart its broker unit, and start the federate units and the field devices |

- The `helics` app and the `ot-sim` app need no change. Without the `helics`
  app, the `ot-sim` app adds a wait script for each broker address
  (`otsim/app.py` lines 269 to 285).
- `sim/cherrymountain_sim` needs no change for the brokers.
- A broker key for each site is optional. It needs `--brokerkey` in the broker
  unit and `HELICS_BROKER_KEY` in `/etc/default/cm-federate-<name>`.
- Change 5 also applies to the single-site range of today.

### What the kit did not test

- ot-sim did not run. The field devices are stand-ins that subscribe and send
  as `helics.json` lists. Nobody knows whether a real I/O module joins again
  after its broker restarts, or how it takes a broker key.
- phenix and minimega did not run. Nobody wrote the changes to the
  `cherrymountain` app. The kit did not load the MGMT nftables policy.
- All hosts of a site share one namespace pair, and all sites share 4 CPUs.
  The kit measured no load of a site on a VM.
- Each federation ran for about one minute. The kit did not run a federation
  to its end time.
- The kit observed that a refusal drops the first core, with helics 3.6.1 and
  the zmq core. It did not read the HELICS source for the cause.
- The ground-truth store is off in the lab. Each site's `helics.json` already
  points it at the site's own `sim-ctl`.
