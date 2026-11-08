# Spike: FUXA offline plugins and secure-mode provisioning

**Question.** Does a pinned FUXA release (a) run with `modbus-serial`,
`node-opcua`, `node-snap7`, and `node-bacnet` baked in and no internet, (b)
accept the secure sign-in, password change, `POST /api/project` boot flow with
tokens that survive a restart, and (c) talk OPC UA and S7 to the OpenPLC v4
plugins? **Decides:** the FUXA image recipe and boot script (DESIGN §12.2).

## Prerequisites

- FUXA VM or container pinned to one release (v1.3.2 or later; record it).
- `mysettings.json` from this directory injected before first start (set a
  real `secretCode`). TODO(verify): file location (`_appdata/` vs `/etc`) and
  the key names for the pinned release; `secureEnabled`, `secretCode`,
  `nodeRedEnabled` are the names used in DESIGN §12.2.
- For step 5: the OpenPLC v4 PLC from the `openplc-v4` spike with `opcua`
  and `s7comm` plugins enabled.

Upstream facts used (read from frangoteam/FUXA `server/api`): `POST
/api/signin` returns `data.token`; protected routes take `x-access-token`;
`POST /api/users` takes `{params: ...}` and needs admin; `POST /api/project`
needs admin and restarts the runtime.

## Steps

1. Start FUXA with `mysettings.json`. On the VM, `./plugin_check.sh`.
2. Disconnect the VM from any package source and repeat step 1 (offline).
3. `FUXA=http://<fuxa>:1881 ./secure_flow.sh`. Run it a second time to check
   idempotence (the re-run path signs in with the new password).
4. Restart FUXA (`systemctl restart` or container restart). Re-run step 3's
   final check: does the earlier token still work (`secretCode` effect)?
5. Export a tiny project from the FUXA editor with one OPC UA device and one
   S7 device pointed at the PLC. POST it with `PROJECT=export.json
   ./secure_flow.sh`. In the editor, confirm both devices show connected and
   tags update. Cross-check with `./opcua_s7_probe.py <plc>`.
6. Note the FUXA RSS after 10 minutes (`ps -o rss`).

## Pass / fail

**Go** when all hold:

- [ ] `plugin_check.sh` prints PASS online and offline.
- [ ] `secure_flow.sh` ends in PASS twice; the seeded password stops working.
- [ ] A project POSTed by the script is shown by the UI with views intact.
- [ ] Tokens survive a restart (or the boot script can re-sign-in; record which).
- [ ] FUXA OPC UA and S7 devices read live values from the v4 plugins.

Failures in the last item only: keep v4 but change that link to Modbus
(DESIGN §12.1 fallback wording) and record it. Failures in the first three
block the image recipe.

## Result

- **Decision:** go / go with changes / no-go
- **FUXA release:**
- **Evidence:** (script output, screenshots of device status)
- **Follow-ups:** (settings file path, users body shape, project schema notes)
