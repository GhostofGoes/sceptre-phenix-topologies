### Spike bbmd (netns): FAIL

- PASS: unicast ReadProperty to device 3003 crosses the stateful ACL (client rule + replies)
- PASS: without BBMDs a Who-Is broadcast from OPS does not find device 3003
- FAIL: with a BBMD on each side, a Who-Is broadcast from OPS finds device 3003 across the router
- FAIL: ReadProperty on the discovered device works through the router
- PASS: removing the BBMD-to-BBMD rule breaks discovery again

- one_way_acl_result: found=False via=[] name='' raw={"iams": [], "read": null}
- router: Linux network namespace with nftables (stand-in for VyOS)
- nft: nftables v1.0.9 (Old Doc Yak #3)
- kernel: 6.18.44-fc-v310
- bacpypes3: Version: 0.0.110
