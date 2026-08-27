# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Check the package works from an installed layout, with nothing else on the path.

Run this with the working directory somewhere other than the source tree, and
with only the install directory on the module search path:

    mkdir -p /tmp/mplib && cp -r mpsystemd /tmp/mplib/
    cd /tmp && MICROPYPATH=".frozen:/tmp/mplib" micropython .../smoke_installed.py
    cd /tmp && PYTHONPATH=/tmp/mplib python3 .../smoke_installed.py
"""

import asyncio

import mpsystemd
from mpsystemd import journal, signals, watchdog
from mpsystemd.testing import FakeNotifyListener


def expect(condition, note):
    if not condition:
        raise SystemExit("smoke check failed: " + note)
    print("  ok %s" % note)


print(
    "mpsystemd %s, MicroPython=%s" % (mpsystemd.__version__, mpsystemd.MICROPYTHON)
)

expect(mpsystemd.notify_available() is False, "no notification socket outside systemd")
expect(mpsystemd.listen_fds() == 0, "no activation descriptors outside systemd")
expect(mpsystemd.watchdog_enabled() == 0, "no watchdog outside systemd")
expect(isinstance(mpsystemd.booted(), bool), "booted() answers")
expect(mpsystemd.monotonic_us() > 0, "monotonic_us() answers")

with FakeNotifyListener() as listener:
    with mpsystemd.Notifier(listener.address) as notifier:
        notifier.ready("smoke")
    expect(
        listener.receive(1000).fields() == {"READY": "1", "STATUS": "smoke"},
        "notification reached a bound socket",
    )

expect(
    journal.encode_field("MESSAGE", "a\nb").startswith(b"MESSAGE\n"),
    "journal length prefixed field form",
)
expect(watchdog.watchdog_period_ms(30000000) == 15000, "watchdog period arithmetic")


async def signal_round_trip():
    from mpsystemd import _platform

    async with signals.SignalWatcher(signals.SIGUSR1, poll_ms=1) as watcher:
        _platform.kill(_platform.getpid(), signals.SIGUSR1)
        return await watcher.wait()


expect(asyncio.run(signal_round_trip()) == signals.SIGUSR1, "signal round trip")
print("installed layout smoke checks passed")
