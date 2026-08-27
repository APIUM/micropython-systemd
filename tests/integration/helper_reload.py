# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""A Type=notify-reload service used to check the reload handshake.

systemctl reload only succeeds when RELOADING=1 arrives with a MONOTONIC_USEC no
older than the moment systemd asked for the reload, and READY=1 follows it.
"""

import asyncio
import sys

import mpsystemd
from mpsystemd import signals

MARKER = sys.argv[1]
POLL_MS = 20


async def main():
    reloads = 0
    mpsystemd.ready("serving")
    wanted = (signals.SIGHUP, signals.SIGTERM, signals.SIGINT)
    async with signals.SignalWatcher(wanted, poll_ms=POLL_MS) as watcher:
        while True:
            signo = await watcher.wait()
            if signo == signals.SIGHUP:
                mpsystemd.reloading("re-reading configuration")
                await asyncio.sleep(0.2)
                reloads += 1
                mpsystemd.ready("serving after %d reloads" % reloads)
                continue
            break
    mpsystemd.stopping()
    with open(MARKER, "w") as marker:
        marker.write("reloads=%d\n" % reloads)


asyncio.run(main())
