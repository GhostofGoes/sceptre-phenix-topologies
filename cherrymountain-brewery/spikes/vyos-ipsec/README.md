# Spike: IPsec and DNP3 through VyOS 1.5

**Question.** Does phenix's `vyatta.tmpl` IPsec block commit on VyOS 1.5, and
does DNP3 (TCP 20000) cross the resulting tunnel? **Decides:** use phenix as
is, patch phenix, or push the IPsec config through cc (DESIGN §10.3, §19).

## Known issue found while writing this kit

In `src/go/tmpl/templates/vyatta.tmpl` (sandialabs/sceptre-phenix, `main`),
the set-command IPsec block writes the IKE proposal under the wrong group:

```text
set vpn ipsec ike-group IKE-1W lifetime 3600
set vpn ipsec esp-group IKE-1W proposal 1 encryption aes256   <- should be ike-group
set vpn ipsec esp-group IKE-1W proposal 1 hash sha1           <- should be ike-group
```

That creates a bogus `esp-group IKE-1W`, leaves `ike-group IKE-1W` without a
proposal, and the commit is expected to fail on 1.5 (TODO(verify): confirm on
your image; older VyOS may have tolerated it). `patches/vyatta-ike-group.patch`
fixes it. Other 1.5 incompatibilities (for example `key-exchange`, interface
naming) are unverified; `commit_check.sh` finds them.

## Prerequisites

- VyOS 1.5 image in phenix, and a linux image (`topology.yml` placeholders).
- For the patch path: a phenix source tree you can rebuild.

## Steps

1. Create the experiment from `topology.yml` + `scenario.yml`; start it.
   The vrouter app renders the config at boot. Record `show version` on both
   routers (must be 1.5).
2. On rtr-edge, find phenix's generated script and run it through the
   committer: `vbash ./commit_check.sh <generated.sh>` (copy the file in via
   `phenix cc` or VNC paste). Note the first failing command.
3. If it fails, reset (`configure; load /config/config.boot`) and run the
   manual known-good set: `vbash ./commit_check.sh --manual 203.0.113.2
   203.0.113.3 10.20.15.0/24 192.0.2.0/24 <psk>`; repeat on rtr-util with the
   values swapped, same PSK.
4. Confirm `show vpn ipsec sa` shows the tunnel up on both sides.
5. From utility-derms: start `nc -l 20000` or an ot-sim outstation on
   rtu-der, then `./tunnel_check.sh 10.20.15.10`.
6. Patch path: apply `patches/vyatta-ike-group.patch` to a phenix tree,
   rebuild, restart the experiment, and repeat step 2 with the generated
   script.
7. cc path: write the manual set as a script and push it with a
   `phenix cc` exec (or the `scorch`/startup inject) after boot; confirm it
   survives a reboot (`save`).

## Pass / fail

- [ ] Stock template output commits cleanly on 1.5 (go as-is), **or**
- [ ] patched template output commits (go with patch), **or**
- [ ] the manual set commits and is deliverable via cc (go with cc).
- [ ] In all cases: SA up both sides, ICMP and DNP3 link status cross the
      tunnel, and `tcpdump` on the WAN side shows ESP only.

None of the three paths commits: **no-go**; fall back to a routed (no IPsec)
utility link and record the compromise in §10.3.

## Result

- **Decision:** as-is / patch phenix / push via cc / no-go
- **VyOS build:**
- **Evidence:** (first failing command, commit output, `show vpn ipsec sa`)
- **Follow-ups:** (upstream phenix PR for the ike-group line; other template issues)
