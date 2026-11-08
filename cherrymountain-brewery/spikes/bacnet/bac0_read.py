#!/usr/bin/env python3
"""ReadProperty with BAC0 (the library on the EWS), as a cross-check.

Usage: bac0_read.py LOCAL_IP/MASK TARGET_IP [device_id]
Install: pip install BAC0
TODO(verify): BAC0 2025+ moved to an asyncio API (`BAC0.start`, awaitable
`read`); older releases use `BAC0.lite` with synchronous `read`. This script
tries the async form and falls back to the sync form.
"""
import asyncio
import sys

import BAC0


async def run_async(local: str, target: str, dev: int) -> None:
    async with BAC0.start(ip=local) as bac:  # TODO(verify)
        print(await bac.read(f"{target} device {dev} objectName"))
        print(await bac.read(f"{target} device {dev} presentValue"))


def run_sync(local: str, target: str, dev: int) -> None:
    bac = BAC0.lite(ip=local)
    print(bac.read(f"{target} device {dev} objectName"))
    bac.disconnect()


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    local, target = sys.argv[1], sys.argv[2]
    dev = int(sys.argv[3]) if len(sys.argv) > 3 else 3003
    try:
        asyncio.run(run_async(local, target, dev))
    except (AttributeError, TypeError):
        run_sync(local, target, dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
