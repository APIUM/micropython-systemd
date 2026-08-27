# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""An asyncio task that services the systemd watchdog.

systemd expects WATCHDOG=1 well inside the WatchdogSec window. The documented
convention is to send it at half the interval, which leaves room for one lost
message or one late scheduling slot.
"""

import asyncio

from ._aio import sleep_ms
from .daemon import Notifier, watchdog_enabled

DEFAULT_DIVISOR = 2

_USEC_PER_MS = 1000
MIN_PERIOD_MS = 1


def watchdog_period_ms(interval_us, divisor=DEFAULT_DIVISOR):
    """Return the ping period in milliseconds for a watchdog interval."""
    if divisor < 1:
        raise ValueError("divisor must be at least 1")
    period = interval_us // divisor // _USEC_PER_MS
    if period < MIN_PERIOD_MS:
        return MIN_PERIOD_MS
    return period


async def watchdog_loop(interval_us=None, divisor=DEFAULT_DIVISOR, notifier=None):
    """Send WATCHDOG=1 forever at interval_us / divisor.

    With interval_us left as None the interval comes from WATCHDOG_USEC, and the
    coroutine returns straight away when no watchdog is configured for this
    process. One Notifier is held open for the whole run rather than a socket
    being opened per ping.
    """
    if interval_us is None:
        interval_us = watchdog_enabled()
    if not interval_us:
        return
    period_ms = watchdog_period_ms(interval_us, divisor)
    owned = notifier is None
    if owned:
        notifier = Notifier()
    try:
        if not notifier.available:
            return
        while True:
            notifier.watchdog_ping()
            await sleep_ms(period_ms)
    finally:
        if owned:
            notifier.close()


def start_watchdog(interval_us=None, divisor=DEFAULT_DIVISOR, notifier=None):
    """Start watchdog_loop as a task, or return None when there is no watchdog.

    Cancel the returned task to stop pinging.
    """
    if interval_us is None:
        interval_us = watchdog_enabled()
    if not interval_us:
        return None
    return asyncio.create_task(
        watchdog_loop(interval_us=interval_us, divisor=divisor, notifier=notifier)
    )
