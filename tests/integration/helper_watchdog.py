# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""A Type=notify service that services the watchdog and then stops on purpose.

The driver watches the unit survive several watchdog intervals and then fail with
Result=watchdog once the pings stop, which shows both that the pings were
reaching systemd and that the watchdog was real.
"""

import asyncio
import sys

import mpsystemd
from mpsystemd import watchdog

MARKER = sys.argv[1]
PING_SECONDS = float(sys.argv[2])


async def main():
    interval_us = mpsystemd.watchdog_enabled()
    with open(MARKER, "w") as marker:
        marker.write("watchdog_usec=%d\n" % interval_us)
        marker.write("period_ms=%d\n" % watchdog.watchdog_period_ms(interval_us))
    mpsystemd.ready("pinging")
    task = watchdog.start_watchdog()
    if task is None:
        mpsystemd.errno(1)
        sys.exit(1)
    await asyncio.sleep(PING_SECONDS)
    task.cancel()
    mpsystemd.status("no longer pinging")
    # Stay alive with the watchdog unserviced so that systemd has to act.
    await asyncio.sleep(3600)


asyncio.run(main())
