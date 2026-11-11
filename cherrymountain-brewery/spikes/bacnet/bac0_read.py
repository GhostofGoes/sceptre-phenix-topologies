#!/usr/bin/env python3
"""ReadProperty with BAC0 (the library on the EWS), as a cross-check.

Usage: bac0_read.py LOCAL_IP/MASK TARGET_IP [device_id]
Install: pip install BAC0
Verified with BAC0 2026.7.25 (asyncio API: `async with BAC0.start(ip=...)`,
awaitable `read`). `presentValue` is read from `analogInput 0`, not from the
device object (BAC0 raises UnknownPropertyError for device/presentValue).
Older releases use `BAC0.lite` with a synchronous `read`; the fallback below
covers them but was not run.
"""
import asyncio
import sys

import BAC0

AI_OBJECT: str = "analogInput 0"


async def run_async(local: str, target: str, dev: int) -> None:
    async with BAC0.start(ip=local) as bac:
        print(await bac.read(f"{target} device {dev} objectName"))
        print(await bac.read(f"{target} {AI_OBJECT} presentValue"))


def run_sync(local: str, target: str, dev: int) -> None:
    bac = BAC0.lite(ip=local)
    print(bac.read(f"{target} device {dev} objectName"))
    print(bac.read(f"{target} {AI_OBJECT} presentValue"))
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
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
