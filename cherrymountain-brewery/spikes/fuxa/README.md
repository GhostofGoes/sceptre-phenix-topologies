# Spike: FUXA offline plugins and secure-mode provisioning

**Question.** Does a pinned FUXA release (a) run with `modbus-serial`,
`node-opcua`, `node-snap7`, and `node-bacnet` baked in and no internet, (b)
accept the secure sign-in, password change, `POST /api/project` boot flow with
tokens that survive a restart, and (c) talk OPC UA and S7 to the OpenPLC v4
plugins? **Decides:** the FUXA image recipe and boot script (DESIGN §12.2).

## Prerequisites

- FUXA VM or container pinned to one release (v1.3.2 or later; record it).
  Run with Docker: `docker run -p 1881:1881 frangoteam/fuxa:1.3.5`.
- `mysettings.json` from this directory injected before first start (set a
  real `secretCode`). Verified at 1.3.5: it lives at
  `server/_appdata/mysettings.json` (in the image
  `/usr/src/app/FUXA/server/_appdata/`, or `<userDir>/_appdata/`) and is read
  at boot by `server/main.js`; `secureEnabled`, `secretCode`, `uiPort`,
  `nodeRedEnabled`, `tokenExpiresIn`, `daqstore` and others are honored.
- For step 5: the OpenPLC v4 PLC from the `openplc-v4` spike with `opcua`
  and `s7comm` plugins enabled.

Upstream facts used (read from frangoteam/FUXA `server/api`): `POST
/api/signin` returns `data.token`; protected routes take `x-access-token`;
`POST /api/users` takes `{params: {username, fullname, password, groups,
info}}` and needs admin; `POST /api/project` needs admin and restarts the
runtime. Drivers are not baked into the image: they install on demand into
`server/_pkg/runtime/node_modules` (see Result).

## Steps

1. Start FUXA with `mysettings.json`. Install the drivers once while online
   (`FUXA=... TOKEN=... ./install_plugins.sh`, which calls `POST /api/plugins`
   per driver, or pre-install into `server/_pkg/runtime`), then run
   `./plugin_check.sh` on the host or in the container.
2. Disconnect from any package source (`docker run --network none` with the
   `_pkg/` directory copied in) and repeat step 1 (offline).
3. `FUXA=http://<fuxa>:1881 ./secure_flow.sh`. Run it a second time to check
   idempotence (the re-run path signs in with the new password).
4. Restart FUXA (`systemctl restart` or container restart). Re-run step 3's
   final check: does the earlier token still work (`secretCode` effect)?
5. Generate a project with one OPC UA, S7, Modbus TCP and BACnet device
   (`./make_project.py project.json --plc <plc> --bacnet <bas> --local-ip
   <fuxa-ip>`), or export one from the FUXA editor. POST it with
   `PROJECT=project.json ./secure_flow.sh`. In the editor, confirm the devices
   show connected and tags update (or `GET /api/getTagValue?ids=[...]`).
   Cross-check with `./opcua_s7_probe.py <plc>`.
6. Note the FUXA RSS after 10 minutes (`ps -o rss`).

## Pass / fail

**Go** when all hold:

- [x] `plugin_check.sh` prints PASS online and offline.
- [x] `secure_flow.sh` ends in PASS twice; the seeded password stops working.
- [ ] A project POSTed by the script is shown by the UI with views intact
      (API read-back checked, browser not).
- [x] Tokens survive a restart (or the boot script can re-sign-in; record which).
- [x] FUXA OPC UA and S7 devices read live values from the v4 plugins.

Failures in the last item only: keep v4 but change that link to Modbus
(DESIGN §12.1 fallback wording) and record it. Failures in the first three
block the image recipe.

## Result

Run in Docker (FUXA container and OpenPLC v4.3.2 container from the
`openplc-v4` spike on one bridge network). Not run: a VM image, the FUXA UI in
a browser, or a 10-minute RSS soak.

- **Decision:** go with changes (the image recipe differs from DESIGN §12.2
  in how drivers are baked in; everything else works). Status: partial, because
  the UI view check was not done.
- **FUXA release:** `frangoteam/fuxa:1.3.5`, digest
  `sha256:931781ce0557304d5b3b51e3d2ddb180dd2252b5e4b9936be7fd935bf5c62a2a`
  (900 MB, Node v18.20.8), source tag v1.3.5 commit
  `cf0a44c750c0ab89ef0e86ade9fb97fd0613ffca`, server version
  `1.3.5-2968`. Docker Hub also has `snap7-<version>` tags (for example `snap7-1.3.0`),
  not tested; the Dockerfile has a `NODE_SNAP` build arg that adds `node-snap7`
  to root `node_modules`.
- **Evidence:**
  - **Plugins.** The stock image contains none of `modbus-serial`,
    `node-opcua`, `node-snap7`, `node-bacnet`. `GET /api/plugins` lists them
    with `current: ''`. `POST /api/plugins` runs `npm install --save-exact`
    into `server/_pkg/runtime` (2 s, 10 s, 1 s, 13 s for modbus-serial,
    node-opcua, node-bacnet, node-snap7 at 8.0.19, 2.149.0, 0.2.4, 1.0.9;
    `_pkg/runtime/node_modules` is 92 MB; node-snap7 ships a prebuilt
    `node_snap7.node`, no compiler needed). Offline: that `_pkg/` copied into a
    fresh container started with `--network none`; after boot `GET
    /api/plugins` showed all four as `current` with `pkg` runtime, and
    `plugin_check.sh` printed `PASS` in both containers.
  - **Settings.** `mysettings.json` goes in `server/_appdata/`; a first boot
    creates `settings.js` there, and `mysettings.json` overrides it in memory
    on every boot (keys honored in `server/main.js`: language, uiPort,
    secureEnabled, secureOnlyEditor, tokenExpiresIn, enableRefreshCookieAuth,
    refreshTokenExpiresIn, secretCode, smtp, daqstore, alarms, logs,
    broadcastAll, lazyViewLoading, logFull, userRole, nodeRedEnabled,
    nodeRedAuthMode, swaggerEnabled).
  - **Sign-in and users.** Seeded `admin`/`123456`; `POST /api/signin` ->
    `{"status":"success",...,"data":{"username":"admin","fullname":"Administrator
    Account","groups":-1,"info":null,"token":"..."}}`. `POST /api/users` body
    `{"params":{"username","fullname","password","groups":-1,"info":"{}"}}` ->
    HTTP 200; the seeded password then fails and the new one works.
    `info` must be a JSON string: omitting it still returns 200 but logs
    `users.usrstorage-set-users failed! SyntaxError: Unexpected token u in
    JSON at position 0` (verified).
  - **secure_flow.sh** PASS on the first run and on re-runs (seeded password
    rejected, falls through to the new one). `POST /api/project` -> HTTP 200;
    the read-back has every posted device, tag and view id. Token still
    valid after the in-process runtime restart, and after a full
    `docker restart` of FUXA (`secretCode` set): HTTP 200, and devices
    reconnected and kept reading.
  - **Project replace semantics.** `POST /api/project` calls
    `prjstorage.clearAll()` first, so the built-in FuxaServer device (id `0`)
    vanishes unless the posted project includes it (`GET` after the first
    POST had no `server` key). Keys like `alarms` or `texts` in the body do
    not read back (the old default project failed its round-trip check for
    that reason; the check now compares devices, tags and views).
  - **Interop (FUXA's own drivers, `GET /api/getTagValue`).** OPC UA tag
    `ns=2;s=PLC.cnt` 15696 -> 15797; S7 tag `DB1.DBW0` (type Int) 15750 ->
    15850; Modbus TCP tag (`memaddress` 400000, address 1) 15750 -> 15850;
    BACnet tag `0-0` 600 -> 700 ... 1200. All against OpenPLC v4.3.2
    plugins and the BACpypes3 responder.
    `opcua_s7_probe.py`: `ok opcua ... Objects children ['Locations',
    'Server', 'Aliases', 'cnt', 'inp']`, `ok s7 ... DB1[0:4]=...`, PASS.
  - **Memory.** FUXA process RSS 174 MB after the restart with four devices
    polling at 1 s (docker stats 131 MiB). Not the 10-minute figure.
- **Follow-ups:**
  - DESIGN §12.2 says to bake the four drivers into the image. In 1.3.5 they
    are runtime packages: the recipe is to run the four `POST /api/plugins`
    installs once at image build (or `npm install --prefix server/_pkg/runtime
    --save-exact <pkg>@<ver>`) and ship `server/_pkg/`, or add them to root
    `node_modules` (`NODE_SNAP=true` does that for snap7 only).
  - Send `info: "{}"` in the users call; seeded admin `groups` is `-1`.
  - Include the FuxaServer device in generated projects.
  - Tag address formats: OPC UA node id string; S7 `DB<n>.DBW<byte>`; Modbus
    1-based address with `memaddress` 400000/300000/100000/0; BACnet
    `<type>-<instance>` with `memaddress` = remote device instance.
  - The BACnet device `property.address` is the FUXA host's own IP, and
    FUXA's BACnet connect needs a broadcast Who-Is answered (see the bacnet
    spike).
  - OpenPLC OPC UA path `/openplc/opcua` is optional for asyncua but keep it
    consistent with the FUXA device address.
  - Left undone: confirm in a browser that the posted view renders in the
    editor; the 10-minute RSS sample.
  - Round 2 (the [bbmd](../bbmd/README.md) spike): the same FUXA image and
    `install_plugins.sh`, `secure_flow.sh` and `make_project.py` also provisioned a
    FUXA on a lab subnet behind a VyOS firewall (`install_plugins.sh` takes
    `ONLY="node-bacnet"`; `make_project.py` takes `--bacnet-only` and
    `--broadcast-address none|ADDR`). The BACnet device's `broadcastAddress`
    decides where FUXA's Who-Is goes; see that README before choosing it.
