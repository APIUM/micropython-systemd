# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Watchdog interval, PID ownership and servicing tests. No systemd needed."""

import harness as h

from mpsystemd import _platform, watchdog
from mpsystemd.daemon import Notifier, watchdog_enabled
from mpsystemd.testing import FakeNotifyListener

ONE_SECOND_US = 1000000


def _set(usec=None, pid=None):
    for name in ("WATCHDOG_USEC", "WATCHDOG_PID"):
        _platform.unsetenv(name)
    if usec is not None:
        _platform.setenv("WATCHDOG_USEC", str(usec))
    if pid is not None:
        _platform.setenv("WATCHDOG_PID", str(pid))


def _clear():
    _set()
    _platform.unsetenv("NOTIFY_SOCKET")


# --- interval and ownership ------------------------------------------------


def test_no_watchdog_when_environment_is_empty():
    _clear()
    h.eq(watchdog_enabled(), 0)


def test_interval_comes_from_watchdog_usec():
    _set(usec=30 * ONE_SECOND_US)
    try:
        h.eq(watchdog_enabled(), 30000000)
    finally:
        _clear()


def test_interval_honoured_when_watchdog_pid_matches():
    _set(usec=ONE_SECOND_US, pid=_platform.getpid())
    try:
        h.eq(watchdog_enabled(), ONE_SECOND_US)
    finally:
        _clear()


def test_no_watchdog_when_watchdog_pid_belongs_to_another_process():
    _set(usec=ONE_SECOND_US, pid=_platform.getpid() + 1)
    try:
        h.eq(watchdog_enabled(), 0)
    finally:
        _clear()


def test_no_watchdog_for_unparseable_interval():
    _set(usec="thirty")
    try:
        h.eq(watchdog_enabled(), 0)
    finally:
        _clear()


def test_no_watchdog_for_zero_interval():
    _set(usec=0)
    try:
        h.eq(watchdog_enabled(), 0)
    finally:
        _clear()


def test_no_watchdog_for_unparseable_pid():
    _set(usec=ONE_SECOND_US, pid="me")
    try:
        h.eq(watchdog_enabled(), 0)
    finally:
        _clear()


def test_unset_environment_clears_both_variables():
    _set(usec=ONE_SECOND_US, pid=_platform.getpid())
    try:
        h.eq(watchdog_enabled(unset_environment=True), ONE_SECOND_US)
        h.eq(_platform.getenv("WATCHDOG_USEC"), None)
        h.eq(_platform.getenv("WATCHDOG_PID"), None)
        h.eq(watchdog_enabled(), 0)
    finally:
        _clear()


def test_unset_environment_clears_even_on_pid_mismatch():
    _set(usec=ONE_SECOND_US, pid=_platform.getpid() + 1)
    try:
        h.eq(watchdog_enabled(unset_environment=True), 0)
        h.eq(_platform.getenv("WATCHDOG_USEC"), None)
        h.eq(_platform.getenv("WATCHDOG_PID"), None)
    finally:
        _clear()


# --- ping period -----------------------------------------------------------


def test_period_is_half_the_interval():
    h.eq(watchdog.watchdog_period_ms(30 * ONE_SECOND_US), 15000)


def test_period_honours_a_different_divisor():
    h.eq(watchdog.watchdog_period_ms(30 * ONE_SECOND_US, divisor=3), 10000)


def test_period_never_drops_below_one_millisecond():
    h.eq(watchdog.watchdog_period_ms(100), watchdog.MIN_PERIOD_MS)


def test_period_rejects_a_zero_divisor():
    h.raises(ValueError, watchdog.watchdog_period_ms, ONE_SECOND_US, 0)


# --- servicing loop --------------------------------------------------------


def test_loop_returns_at_once_when_no_watchdog_is_configured():
    _clear()

    async def body():
        await watchdog.watchdog_loop()
        return "returned"

    h.eq(h.run_async(body()), "returned")


def test_start_watchdog_returns_none_when_no_watchdog_is_configured():
    _clear()

    async def body():
        return watchdog.start_watchdog()

    h.eq(h.run_async(body()), None)


def test_loop_pings_at_half_the_interval():
    """A 200 ms interval must produce pings about every 100 ms."""
    import asyncio

    interval_us = 200000
    run_ms = 450

    with FakeNotifyListener() as listener:
        async def body():
            notifier = Notifier(listener.address)
            task = asyncio.create_task(
                watchdog.watchdog_loop(interval_us=interval_us, notifier=notifier)
            )
            await asyncio.sleep(run_ms / 1000)
            task.cancel()
            notifier.close()

        h.run_async(body())
        received = listener.receive_all(200)

    for datagram in received:
        h.eq(datagram.fields(), {"WATCHDOG": "1"})
    # One ping immediately, then one per 100 ms window. Allow one either way for
    # scheduling slack.
    h.between(len(received), 4, 6, "ping count over %d ms" % run_ms)


def test_loop_uses_the_configured_interval_from_the_environment():
    with FakeNotifyListener() as listener:
        _platform.setenv("NOTIFY_SOCKET", listener.address)
        _set(usec=200000, pid=_platform.getpid())
        try:
            import asyncio

            async def body():
                task = watchdog.start_watchdog()
                h.ne(task, None, "start_watchdog returned no task")
                await asyncio.sleep(0.25)
                task.cancel()

            h.run_async(body())
            received = listener.receive_all(200)
            h.between(len(received), 2, 4, "ping count over 250 ms")
        finally:
            _clear()
