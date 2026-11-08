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

- **Decision:** **go, use phenix as is** (the pinned fork already carries the
  `ike-group` fix; no patch and no cc push needed). Two template follow-ups
  below.
- **VyOS build:** `VyOS 999.202611081848`, release train `rolling`, flavor
  `qemu`, built in CI from `vyos-build` branch `rolling` by the images repo's
  `scripts/vyos/build-vyos.sh` with miniccc injected. Rolling images report
  `999.<stamp>`, not `1.5`: they are the line that becomes 1.5/1.6, not a 1.5
  release. DESIGN says "VyOS 1.5"; see Follow-ups.
- **Evidence** (run [42513925243](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42513925243), job "Spike vyos-ipsec", artifact
  `evidence-vyos-ipsec`; phenix `984869d` of GhostofGoes/sceptre-phenix
  `ci-e2e-minimega`, minimega 3.3.0):
  - phenix rendered `rtr-edge.boot` and `rtr-util.boot`; the image applied them
    at first boot and both routers logged `Successful change to active
    configuration`. The rendered IPsec block has `ike-group IKE-1W proposal 1
    encryption aes256` / `hash sha1` (the upstream bug is not in this phenix).
  - `show vpn ipsec sa` on both routers: `rtr-util-tunnel-0  up
    AES_CBC_256/HMAC_SHA1_96`.
  - From utility-derms (192.0.2.10): ICMP to rtu-der (10.20.15.10) 0% loss,
    TCP 20000 open, and `dnp3_link.py` got a valid DNP3 link-status frame (dest
    3, src 4).
  - A capture on the host tap of rtr-edge's WAN NIC (outside the VM) showed 28
    ESP packets and no cleartext ICMP or TCP 20000. A capture inside the router
    is not evidence: the router's own tap also shows the decrypted copy of every
    inbound packet (run 3 counted 5 ICMP and 10 TCP/20000 lines that way).
  - Reproduction of the upstream bug (`esp-group IKE-1W proposal` lines instead
    of `ike-group`, applied on rtr-util): VyOS rolling **commits it**, leaving
    `IKE-1W` with no proposal (defaults), so the stock upstream template does
    not fail the commit as this README first guessed. It is still wrong: the
    two ends would negotiate defaults, not the intended AES-256/SHA-1.
- **Follow-ups:**
  - The IKE group has no `key-exchange`, and VyOS prints `DEPRECATION WARNING:
    IKE group "IKE-1W" has no key-exchange set and will still accept incoming
    deprecated IKEv1 connections`. Send `set vpn ipsec ike-group IKE-1W
    key-exchange ikev2` upstream (`commit_check.sh --manual` already uses it).
  - Upstream PR for the `ike-group` line in sandialabs/sceptre-phenix
    `vyatta.tmpl` (if `main` still has it); `patches/vyatta-ike-group.patch`
    remains the diff.
  - Pin or document the VyOS build: `rolling` moves daily. The images repo's
    `build-vyos.sh` needed two fixes in CI (its image-name parsing and its NIC
    ordering patch no longer match vyos-build and vyos-1x); `spikes/ci/build_image.sh`
    works around both and should be upstreamed to sceptre-phenix-images.
  - Run the path again with ot-sim DNP3 instead of the link-layer stub once the
    ot-sim image exists (the otsim-noble spike covers DNP3 itself).
