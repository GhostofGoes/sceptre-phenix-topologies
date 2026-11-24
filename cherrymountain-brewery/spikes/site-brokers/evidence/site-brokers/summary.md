# site-brokers

## Checks

| Check | Result | Detail |
|---|---|---|
| ci: 4 federations with the same federate names run at once, one broker each | pass | each root broker lists ['cellar', 'rio-cellar']; fr: ['cellar', 'rio-cellar'], fc: ['cellar', 'rio-cellar'], ep: ['cellar', 'rio-cellar'], cs: ['cellar', 'rio-cellar'] |
| ci: ep restarts alone, and the other sites go on | pass | window 20.0 s; fr: 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s; fc: 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s; cs: 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s; ep (restarted): down 6 s, then 9.0 s until step 10; its time starts again at 1 |
| ci: each federation receives exactly the values of its own site's cellar, through the restart of a neighbor | pass | fr: 1023 values over 31 steps, 0 not equal; fc: 1419 values over 43 steps, 0 not equal; ep: 858 values over 26 steps, 0 not equal; cs: 1023 values over 31 steps, 0 not equal; ep, before its restart: 363 values over 11 steps, 0 not equal; the comparison covers the 20 s of the neighbor's restart |
| ci: a write to `cellar/updates` changes the setpoint at the site that sent it, and at no other | pass | `cellar/fv101.spund_sp_psia` at the end: {'fr': 17.25, 'fc': 13.096147841033412, 'ep': 13.096147841033412, 'cs': 18.5}; written: {'fr': 17.25, 'cs': 18.5}; the setpoint of each site's model with no write: {'fr': 13.096147841033412, 'fc': 13.096147841033412, 'ep': 13.096147841033412, 'cs': 13.096147841033412} |
| full: 4 federations (19, 19, 19, 19 federates) run at once, and 19 federate names are in all of them | pass | in every federation: bas-coldroom, bas-rtu-taproom, bas-vent, bess-pcs, brewhouse, building, cellar, gen-ats, glycol-chiller, packaging, power, pv-inverter, relay-main, rio-brewhouse, rio-cellar, rio-packaging, rio-utilities, utilities, weather; fr: 7 real federates and 12 stand-ins, health ok; fc: 7 real federates and 12 stand-ins, health ok; ep: 7 real federates and 12 stand-ins, health ok; cs: 7 real federates and 12 stand-ins, health ok |
| full: every stand-in receives its own site's setpoint and calendar day, and never another site's | pass | fr: setpoint [13.13643] in 12 values, day [20649.0] in 15 values; fc: setpoint [13.226234] in 12 values, day [20650.0] in 15 values; ep: setpoint [13.567801] in 12 values, day [20651.0] in 15 values; cs: setpoint [12.762159] in 12 values, day [20652.0] in 15 values |
| full: a scenario event at one site acts on that site's plant, and on no other | pass | fr: event FV-102, FV-102 40 g/s, FV-103 0 g/s; fc: event none, FV-102 0 g/s, FV-103 0 g/s; ep: event none, FV-102 0 g/s, FV-103 0 g/s; cs: event FV-103, FV-102 0 g/s, FV-103 40 g/s |
| full: ep restarts alone, and the other sites go on | pass | window 20.9 s; fr: 21 steps, t 28 to 48, 0 irregular, longest gap 1.02 s; fc: 21 steps, t 28 to 48, 0 irregular, longest gap 1.01 s; cs: 21 steps, t 28 to 48, 0 irregular, longest gap 1.02 s; ep (restarted): down 6 s, then 9.8 s until step 10; its time starts again at 1 |
| full: after the restart of ep, every other site's time went on and its event API reports `ok` | pass | fr: t_s 25 to 49, ok, 2026-07-15T04:30:49-06:00; fc: t_s 25 to 49, ok, 2026-07-16T04:30:50-06:00; cs: t_s 25 to 49, ok, 2026-07-18T04:30:50-06:00; ep (restarted): t_s stopped to 12, ok, 2026-07-17T04:30:11-06:00 |
| fleet: 4 federations (19, 12, 19, 14 federates) run at once, and 5 federate names are in all of them | pass | in every federation: brewhouse, building, cellar, utilities, weather; fr: 7 real federates and 12 stand-ins, health ok; fc: 5 real federates and 7 stand-ins, health ok; ep: 7 real federates and 12 stand-ins, health ok; cs: 6 real federates and 8 stand-ins, health ok |
| fleet: every stand-in receives its own site's setpoint and calendar day, and never another site's | pass | fr: setpoint [13.13643] in 12 values, day [20649.0] in 15 values; fc: setpoint [13.226234] in 13 values, day the site has no power area; ep: setpoint [13.567801] in 12 values, day [20651.0] in 15 values; cs: setpoint [12.762159] in 12 values, day the site has no power area |
| fleet: a scenario event at one site acts on that site's plant, and on no other | pass | fr: event FV-102, FV-102 40 g/s, FV-103 0 g/s; fc: event none, FV-102 0 g/s, FV-103 0 g/s; ep: event none, FV-102 0 g/s, FV-103 0 g/s; cs: event FV-103, FV-102 0 g/s, FV-103 40 g/s |
| fleet: ep restarts alone, and the other sites go on | pass | window 21.4 s; fr: 22 steps, t 28 to 49, 0 irregular, longest gap 1.16 s; fc: 21 steps, t 29 to 49, 0 irregular, longest gap 1.02 s; cs: 21 steps, t 29 to 49, 0 irregular, longest gap 1.01 s; ep (restarted): down 6 s, then 10.3 s until step 10; its time starts again at 1 |
| fleet: after the restart of ep, every other site's time went on and its event API reports `ok` | pass | fr: t_s 25 to 49, ok, 2026-07-15T04:30:50-06:00; fc: t_s 26 to 50, ok, 2026-07-16T04:30:50-06:00; cs: t_s 26 to 50, ok, 2026-07-18T04:30:51-06:00; ep (restarted): t_s stopped to 12, ok, 2026-07-17T04:30:11-06:00 |
| shared network: each broker bound to its own site's address keeps two federations apart on the default port | pass | brokers with `--local_interface <the site's sim-ctl address>`, federates with their site's `--broker_address`; listeners: 172.16.10.1:23404, 172.16.10.1:23405, 172.16.10.1:23492, 172.16.10.1:23493, 172.18.10.1:23404, 172.18.10.1:23405, 172.18.10.1:23414, 172.18.10.1:23415; fr: 363 values over 11 steps, 0 not equal; ep: 528 values over 16 steps, 0 not equal |
| shared network: a port for each site keeps two federations apart on one address | pass | brokers with `--port 23404` and `--port 23504` on all addresses, federates with HELICS_BROKER_PORT in their environment; listeners: 0.0.0.0:23404, 0.0.0.0:23405, 0.0.0.0:23504, 0.0.0.0:23505, 172.16.10.1:23414, 172.16.10.1:23415, 172.18.10.1:23509, 172.18.10.1:23510; fr: 363 values over 11 steps, 0 not equal; ep: 528 values over 16 steps, 0 not equal |

## What HELICS did

| Case | Observed | A way to harm or join another site |
|---|---|---|
| A SIGTERM to the process that the command `helics_broker` starts | `helics_broker` of the pip wheel is a Python script that runs the broker as a child. The script exited (code -15), and the broker went on: it still listens on 0.0.0.0:23404, 0.0.0.0:23405. A signal to the whole process group, as systemd sends to a unit, stops both. | no |
| Where a broker listens | With no interface option: 127.0.0.1:23404, 127.0.0.1:23405. With `--ipv4`, as the `helics` app starts it: every address (see the next row). With the options of the `ot-sim` app (`--ipv4 --web --http_server_args=--http_port=8080 --external`): 0.0.0.0:23404, 0.0.0.0:23405, 0.0.0.0:8080. A server that then asks for 127.0.0.1:8080, as the event API of `sim-ctl` does, gets: no bind ([Errno 98] Address already in use). | no |
| By default, a second broker on the same network and port | `--name ep-sim-ctl -f2 --ipv4` exited with code 252 after 4.6 s ( reply socket giving up tcp://*:23405 / [2026-11-24 08:50:04.759] [console] [error] commERROR\|\|ep-sim-ctl (1)[connecting]::receiver connection failure / Broker is unable to connect). The first broker listens on 0.0.0.0:23404, 0.0.0.0:23405. The app starts the broker with `&`, so nothing reports the exit. | yes |
| By default, a field device of one site that reaches the waiting broker of another site | `rio-cellar` of ep, pointed at 172.18.10.1, joined the federation of `fr-sim-ctl`: True. It received 165 values over 5 steps. In 5 of those steps, every value equals the reference of fr. In 0 steps, every value equals the reference of its own site. The broker's federates: ['cellar', 'rio-cellar']. No message names the wrong site. | yes |
| A federate that a running broker refuses | `rio-cellar` of fr came late to its own broker and got: HelicsException: [-1] broker responded with error: already in initialization mode. After the refusal, the first federate of that federation (`cellar` of fr) exited with code 1 (helics.capi.HelicsException: [-10] publications not allowed outside of execution and initialization state). The broker's log: [info] fr-sim-ctl::got disconnect from cellar_core_W5IBEi(0) / [info] fr-sim-ctl::got disconnect from cellar_core_W5IBEi(1879048192). The broker dropped the first core that had registered, not the one that it refused. | yes |
| A broker key (`--brokerkey`, or HELICS_BROKER_KEY for a federate) | The broker of fr has the key `fr-key`. A stand-in of ep with the key `ep-key` got: HelicsException: [-1] broker responded with error: broker key does not match. It did not join (federates created: []). The same refusal after `cellar` of fr registered with the right key from its environment: that cellar exited with code 1 (helics.capi.HelicsException: [-6] HELICS system failure). A key stops the join. It does not protect the federation that the stranger reached. | yes |
| The real federate takes the broker key from its environment | With HELICS_BROKER_KEY in the federate's environment file and no change to `federate.py`, the federation ran: True (231 values, 0 not equal to the reference). The port check does the same with HELICS_BROKER_PORT. | no |
| A broker name (`--name` at the broker, `--brokername` at the federate) | A stand-in that asked for the broker `ep-sim-ctl` at the address of `fr-sim-ctl` joined: True. The zmq core finds its broker by address and port. It does not compare the name. | yes |
| A restart of the federates alone, with the broker left up | The federation ran 62 steps. Then both federate units restarted (`Restart=always`, `RestartSec=5`), and the broker (`--autorestart`) stayed. In 261 s, `cellar` started 43 times and the stand-in 45 times. The broker's answer: HelicsException: [-1] broker responded with error: already in initialization mode. The federation did not run again. | no |
| A restart of the broker alone, with the federates left up | The federation ran 62 steps. Then the broker stopped, and it started again 2 s later with the same command. In 261 s, the `cellar` unit started its process again 0 times. The federate logged 258 lines with `forced Granted Time`: with HELICS's real-time flag it steps on alone, and it does not register at the new broker. The stand-in started again 0 times (HelicsException: [-101] core lost connection with broker). The federation did not run again. | no |

## ci: one site restarts

| Site | Before the restart | During the restart | Processes that exited |
|---|---|---|---|
| fr | 12 steps, t 1 to 12, 0 irregular, longest gap 1.00 s | 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s | none |
| fc | 12 steps, t 1 to 12, 0 irregular, longest gap 1.00 s | 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s | none |
| cs | 12 steps, t 1 to 12, 0 irregular, longest gap 1.00 s | 20 steps, t 13 to 32, 0 irregular, longest gap 1.00 s | none |
| ep (restarted) | stopped at t 12 | down 6 s, then 9.0 s until step 10; its time starts again at 1 | none |

## ci: received values against the offline reference

| Site | Steps | Values held against its own reference | Not equal | Steps equal to another site's reference |
|---|---|---|---|---|
| fr | 31 | 1023 | 0 | 0 |
| fc | 43 | 1419 | 0 | 0 |
| ep | 26 | 858 | 0 | 0 |
| cs | 31 | 1023 | 0 | 0 |
| ep, before its restart | 11 | 363 | 0 | not counted |

## full: the federations

| Site | Source | Broker | Real federates | Stand-ins for field devices | Federates that the root broker lists |
|---|---|---|---|---|---|
| fr | plant/generated/full/helics.json | fr-sim-ctl at 172.16.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | rio-brewhouse, rio-cellar, rio-packaging, rio-utilities, glycol-chiller, relay-main, pv-inverter, bess-pcs, gen-ats, bas-rtu-taproom, bas-coldroom, bas-vent | 19 |
| fc | plant/generated/full/helics.json | fc-sim-ctl at 172.17.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | rio-brewhouse, rio-cellar, rio-packaging, rio-utilities, glycol-chiller, relay-main, pv-inverter, bess-pcs, gen-ats, bas-rtu-taproom, bas-coldroom, bas-vent | 19 |
| ep | plant/generated/full/helics.json | ep-sim-ctl at 172.18.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | rio-brewhouse, rio-cellar, rio-packaging, rio-utilities, glycol-chiller, relay-main, pv-inverter, bess-pcs, gen-ats, bas-rtu-taproom, bas-coldroom, bas-vent | 19 |
| cs | plant/generated/full/helics.json | cs-sim-ctl at 172.19.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | rio-brewhouse, rio-cellar, rio-packaging, rio-utilities, glycol-chiller, relay-main, pv-inverter, bess-pcs, gen-ats, bas-rtu-taproom, bas-coldroom, bas-vent | 19 |

## full: each site's own inputs, as its federation delivers them

| Site | Elevation (m) | FV-102 setpoint for that elevation (psia) | Received by `rio-cellar` | Calendar day, configured | Received by `gen-ats` | `sim_time` of the event API |
|---|---|---|---|---|---|---|
| fr | 1585 | 13.136430 | [13.13643] in 12 values | 20649 | [20649.0] in 15 values | 2026-07-15T04:30:13-06:00 |
| fc | 1525 | 13.226234 | [13.226234] in 12 values | 20650 | [20650.0] in 15 values | 2026-07-16T04:30:13-06:00 |
| ep | 1300 | 13.567801 | [13.567801] in 12 values | 20651 | [20651.0] in 15 values | 2026-07-17T04:30:14-06:00 |
| cs | 1839 | 12.762159 | [12.762159] in 12 values | 20652 | [20652.0] in 15 values | 2026-07-18T04:30:14-06:00 |

## full: a CO2 release at one site

| Site | Event sent | Events in its API | FV-102 leak (g/s) | FV-103 leak (g/s) |
|---|---|---|---|---|
| fr | FV-102 | 1 | 40 | 0 |
| fc | none | 0 | 0 | 0 |
| ep | none | 0 | 0 | 0 |
| cs | FV-103 | 1 | 0 | 40 |

## full: one site restarts

| Site | Before the restart | During the restart | Processes that exited |
|---|---|---|---|
| fr | 27 steps, t 1 to 27, 0 irregular, longest gap 1.01 s | 21 steps, t 28 to 48, 0 irregular, longest gap 1.02 s | none |
| fc | 27 steps, t 1 to 27, 0 irregular, longest gap 1.02 s | 21 steps, t 28 to 48, 0 irregular, longest gap 1.01 s | none |
| cs | 27 steps, t 1 to 27, 0 irregular, longest gap 1.03 s | 21 steps, t 28 to 48, 0 irregular, longest gap 1.02 s | none |
| ep (restarted) | stopped at t 27 | down 6 s, then 9.8 s until step 10; its time starts again at 1 | none |

## full: the event API of each site around the restart

| Site | `t_s` before | `t_s` after | Health after |
|---|---|---|---|
| fr | 25 | 49 | ok, 2026-07-15T04:30:49-06:00 |
| fc | 25 | 49 | ok, 2026-07-16T04:30:50-06:00 |
| cs | 25 | 49 | ok, 2026-07-18T04:30:50-06:00 |
| ep (restarted) | stopped | 12 | ok, 2026-07-17T04:30:11-06:00 |

## fleet: the federations

| Site | Source | Broker | Real federates | Stand-ins for field devices | Federates that the root broker lists |
|---|---|---|---|---|---|
| fr | plant/generated/fleet/fr/helics.json | fr-sim-ctl at 172.16.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | fr-rio-brewhouse, fr-rio-cellar, fr-rio-packaging, fr-rio-utilities, fr-glycol-chiller, fr-relay-main, fr-pv-inverter, fr-bess-pcs, fr-gen-ats, fr-bas-rtu-taproom, fr-bas-coldroom, fr-bas-vent | 19 |
| fc | plant/generated/fleet/fc/helics.json | fc-sim-ctl at 172.17.10.1 | cellar, weather, brewhouse, utilities, building | fc-rio-brewhouse, fc-rio-cellar, fc-rio-utilities, fc-glycol-chiller, fc-bas-rtu-taproom, fc-bas-coldroom, fc-bas-vent | 12 |
| ep | plant/generated/fleet/ep/helics.json | ep-sim-ctl at 172.18.10.1 | cellar, weather, brewhouse, utilities, packaging, power, building | ep-rio-brewhouse, ep-rio-cellar, ep-rio-packaging, ep-rio-utilities, ep-glycol-chiller, ep-relay-main, ep-pv-inverter, ep-bess-pcs, ep-gen-ats, ep-bas-rtu-taproom, ep-bas-coldroom, ep-bas-vent | 19 |
| cs | plant/generated/fleet/cs/helics.json | cs-sim-ctl at 172.19.10.1 | cellar, weather, brewhouse, utilities, packaging, building | cs-rio-brewhouse, cs-rio-cellar, cs-rio-packaging, cs-rio-utilities, cs-glycol-chiller, cs-bas-rtu-taproom, cs-bas-coldroom, cs-bas-vent | 14 |

## fleet: each site's own inputs, as its federation delivers them

| Site | Elevation (m) | FV-102 setpoint for that elevation (psia) | Received by `rio-cellar` | Calendar day, configured | Received by `gen-ats` | `sim_time` of the event API |
|---|---|---|---|---|---|---|
| fr | 1585 | 13.136430 | [13.13643] in 12 values | 20649 | [20649.0] in 15 values | 2026-07-15T04:30:13-06:00 |
| fc | 1525 | 13.226234 | [13.226234] in 13 values | 20650 | the site has no power area | 2026-07-16T04:30:14-06:00 |
| ep | 1300 | 13.567801 | [13.567801] in 12 values | 20651 | [20651.0] in 15 values | 2026-07-17T04:30:13-06:00 |
| cs | 1839 | 12.762159 | [12.762159] in 12 values | 20652 | the site has no power area | 2026-07-18T04:30:14-06:00 |

## fleet: a CO2 release at one site

| Site | Event sent | Events in its API | FV-102 leak (g/s) | FV-103 leak (g/s) |
|---|---|---|---|---|
| fr | FV-102 | 1 | 40 | 0 |
| fc | none | 0 | 0 | 0 |
| ep | none | 0 | 0 | 0 |
| cs | FV-103 | 1 | 0 | 40 |

## fleet: one site restarts

| Site | Before the restart | During the restart | Processes that exited |
|---|---|---|---|
| fr | 27 steps, t 1 to 27, 0 irregular, longest gap 1.01 s | 22 steps, t 28 to 49, 0 irregular, longest gap 1.16 s | none |
| fc | 28 steps, t 1 to 28, 0 irregular, longest gap 1.00 s | 21 steps, t 29 to 49, 0 irregular, longest gap 1.02 s | none |
| cs | 28 steps, t 1 to 28, 0 irregular, longest gap 1.04 s | 21 steps, t 29 to 49, 0 irregular, longest gap 1.01 s | none |
| ep (restarted) | stopped at t 27 | down 6 s, then 10.3 s until step 10; its time starts again at 1 | none |

## fleet: the event API of each site around the restart

| Site | `t_s` before | `t_s` after | Health after |
|---|---|---|---|
| fr | 25 | 49 | ok, 2026-07-15T04:30:50-06:00 |
| fc | 26 | 50 | ok, 2026-07-16T04:30:50-06:00 |
| cs | 26 | 50 | ok, 2026-07-18T04:30:51-06:00 |
| ep (restarted) | stopped | 12 | ok, 2026-07-17T04:30:11-06:00 |

## Versions and facts

| Item | Value |
|---|---|
| Python | 3.12.3 |
| helics (pip) | 3.6.1 (2025-02-24) |
| helics_broker | 3.6.1 (2025-02-24) |
| Kernel | 6.18.44-fc-v819 |
| CPUs | 4 |
| Sites | fr (broker 172.16.10.1), fc (broker 172.17.10.1), ep (broker 172.18.10.1), cs (broker 172.19.10.1) |
| Parts | ci, full, fleet, shared |
| ci: broker command | <venv>/bin/helics_broker --name fr-sim-ctl -f2 --ipv4 --loglevel summary --logfile <work>/ci-fr-first/helics-root-broker.log --autorestart |
| ci: federate command | <venv>/bin/python -m cherrymountain_sim --config <work>/ci-fr-first/helics.json --federate cellar --broker-address 172.16.10.1 --log-level INFO |
| ci: listeners in each sim-ctl namespace | cmsb-fr-ctl: 0.0.0.0:23404, 0.0.0.0:23405; cmsb-fc-ctl: 0.0.0.0:23404, 0.0.0.0:23405; cmsb-ep-ctl: 0.0.0.0:23404, 0.0.0.0:23405; cmsb-cs-ctl: 0.0.0.0:23404, 0.0.0.0:23405 |
| ci: TCP from cmsb-fr-sim to the broker of fc | no connection ([Errno 101] Network is unreachable) |
| ci: TCP from cmsb-fr-sim to its own broker | open |
| Run time | 328 s |
| Namespaces left after the run | none |
