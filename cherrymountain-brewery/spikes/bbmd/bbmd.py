#!/usr/bin/env python3
"""A BACpypes3 BBMD: a BACnet/IP node that forwards broadcasts to its BDT peers.

Usage (the BDT lists every BBMD, this one included):
  bbmd.py --name bbmd-ops --instance 9001 --address 10.20.30.5/24:47808 \
      --bbmd 10.20.30.5:47808 10.20.60.5:47808
Install: pip install bacpypes3==0.0.110

Entries without a mask behave as /32: the sender unicasts the Forwarded-NPDU
to the peer, and the peer rebroadcasts it on its own subnet (two-hop). That is
the mode that works across a router that does not forward directed broadcasts.
"""

import asyncio

from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser


async def main() -> None:
    args = SimpleArgumentParser().parse_args()
    Application.from_args(args)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
