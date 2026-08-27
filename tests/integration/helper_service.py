# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""A Type=notify service used by the integration tests.

Announces readiness, waits for a barrier, hands a descriptor to the descriptor
store, changes its status on SIGHUP and exits on SIGTERM. Everything it observed
goes into the marker file named on the command line so that the driver can check
it after the unit has gone.
"""

import asyncio
import sys

import mpsystemd
from mpsystemd import _platform, signals

MARKER = sys.argv[1]
READY_STATUS = "serving"
RELOADED_STATUS = "second status"
POLL_MS = 50
BARRIER_TIMEOUT_MS = 5000


async def main():
    observed = {
        "notify_available": mpsystemd.notify_available(),
        "booted": mpsystemd.booted(),
        "watchdog_usec": mpsystemd.watchdog_enabled(),
        "listen_fds": mpsystemd.listen_fds(),
    }
    mpsystemd.ready(READY_STATUS)
    observed["barrier"] = mpsystemd.barrier(timeout_ms=BARRIER_TIMEOUT_MS)

    # systemd watches a stored descriptor and drops it on POLLHUP, so the write
    # end of this pipe stays open for as long as the service runs.
    read_fd, write_fd = _platform.pipe()
    observed["fdstore"] = mpsystemd.fdstore(read_fd, name="state")

    wanted = (signals.SIGHUP, signals.SIGTERM, signals.SIGINT)
    async with signals.SignalWatcher(wanted, poll_ms=POLL_MS) as watcher:
        while True:
            signo = await watcher.wait()
            if signo == signals.SIGHUP:
                mpsystemd.status(RELOADED_STATUS)
                continue
            observed["stop_signal"] = signo
            break
    mpsystemd.stopping()
    _platform.close_fd(read_fd)
    _platform.close_fd(write_fd)

    with open(MARKER, "w") as marker:
        for name in sorted(observed.keys()):
            marker.write("%s=%s\n" % (name, observed[name]))


asyncio.run(main())
