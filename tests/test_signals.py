# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Signal delivery tests. No systemd needed.

Each test sends the signal to this process from inside the watcher's context, so
the signal is either blocked or handled before it can arrive.
"""

import harness as h

from mpsystemd import _platform, signals


def _kill_self(signo):
    _platform.kill(_platform.getpid(), signo)


async def _sleep_a_moment():
    from mpsystemd._aio import sleep_ms

    await sleep_ms(5)


def test_watcher_rejects_an_empty_signal_set():
    h.raises(ValueError, signals.SignalWatcher, ())


def test_watcher_rejects_a_poll_interval_below_one_millisecond():
    h.raises(ValueError, signals.SignalWatcher, signals.SIGUSR1, 0)


def test_watcher_accepts_a_single_signal_number():
    watcher = signals.SignalWatcher(signals.SIGUSR1)
    h.eq(watcher.signals, (signals.SIGUSR1,))


def test_default_poll_interval_is_not_faster_than_200_ms():
    h.true(signals.DEFAULT_POLL_MS >= 200)


def test_shutdown_set_is_sigterm_and_sigint():
    h.eq(signals.SHUTDOWN_SIGNALS, (signals.SIGTERM, signals.SIGINT))


def test_pending_is_none_before_any_signal_arrives():
    async def body():
        async with signals.SignalWatcher(signals.SIGUSR2, poll_ms=1) as watcher:
            return watcher.pending()

    h.eq(h.run_async(body()), None)


def test_a_sent_signal_is_reported():
    async def body():
        async with signals.SignalWatcher(signals.SIGUSR1, poll_ms=1) as watcher:
            _kill_self(signals.SIGUSR1)
            return await watcher.wait()

    h.eq(h.run_async(body()), signals.SIGUSR1)


def test_each_signal_in_the_set_is_reported():
    async def body():
        wanted = (signals.SIGUSR1, signals.SIGUSR2)
        async with signals.SignalWatcher(wanted, poll_ms=1) as watcher:
            _kill_self(signals.SIGUSR1)
            first = await watcher.wait()
            _kill_self(signals.SIGUSR2)
            second = await watcher.wait()
            return sorted([first, second])

    h.eq(h.run_async(body()), [signals.SIGUSR1, signals.SIGUSR2])


def test_a_signal_outside_the_set_is_not_reported():
    async def body():
        async with signals.SignalWatcher(signals.SIGUSR2, poll_ms=1) as watcher:
            _kill_self(signals.SIGCONT)
            for _ in range(5):
                if watcher.pending() is not None:
                    return "reported"
                await _sleep_a_moment()
            return "not reported"

    h.eq(h.run_async(body()), "not reported")


def test_wait_for_shutdown_reports_sigterm():
    """SIGTERM would terminate the process if the watcher were not in place."""

    async def body():
        async with signals.SignalWatcher(signals.SIGTERM, poll_ms=1) as watcher:
            _kill_self(signals.SIGTERM)
            return await watcher.wait()

    h.eq(h.run_async(body()), signals.SIGTERM)


def test_wait_for_signals_opens_and_closes_its_own_watcher():
    import asyncio

    async def body():
        task = asyncio.create_task(
            signals.wait_for_signals(signals.SIGUSR1, poll_ms=1)
        )
        # Give the watcher a chance to install itself before signalling.
        await asyncio.sleep(0.05)
        _kill_self(signals.SIGUSR1)
        return await task

    h.eq(h.run_async(body()), signals.SIGUSR1)


def test_pending_outside_the_context_is_an_error():
    from mpsystemd.errors import SystemdError

    watcher = signals.SignalWatcher(signals.SIGUSR1)
    h.raises(SystemdError, watcher.pending)


def test_constants_agree_with_the_signal_module():
    if _platform.MICROPYTHON:
        h.skip("the MicroPython unix port has no signal module to compare against")
    import signal

    for name in (
        "SIGHUP",
        "SIGINT",
        "SIGQUIT",
        "SIGABRT",
        "SIGUSR1",
        "SIGUSR2",
        "SIGPIPE",
        "SIGALRM",
        "SIGTERM",
        "SIGCHLD",
        "SIGCONT",
    ):
        h.eq(getattr(signals, name), int(getattr(signal, name)), name)
