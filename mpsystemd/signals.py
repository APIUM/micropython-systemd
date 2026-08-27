# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Waiting for signals under both runtimes.

On CPython the running event loop delivers signals. On the MicroPython unix port
there is no signal module at all, so signals are blocked with sigprocmask and
read from a signalfd(2) descriptor. A signal handler written as an ffi callback
would re-enter the interpreter from signal context, which is not safe, so this
package never offers one.
"""

from . import _platform
from ._aio import sleep_ms

# Signal numbers on Linux for the architectures the unix port runs on.
SIGHUP = 1
SIGINT = 2
SIGQUIT = 3
SIGABRT = 6
SIGUSR1 = 10
SIGUSR2 = 12
SIGPIPE = 13
SIGALRM = 14
SIGTERM = 15
SIGCHLD = 17
SIGCONT = 18

# What systemd sends for a stop request, plus the terminal interrupt.
SHUTDOWN_SIGNALS = (SIGTERM, SIGINT)

# A service runs for weeks on hardware with one slow core, so the default
# cadence favours idleness over reaction time.
DEFAULT_POLL_MS = 200


class SignalWatcher:
    """An async context manager that reports signals as they arrive.

    Enter it before any of the signals it names could arrive. On the MicroPython
    unix port leaving it restores the previous signal mask, and any named signal
    that arrived but was not read is delivered at that moment with its default
    action.

    poll_ms sets how often the signalfd is checked on MicroPython. CPython is
    woken by the event loop and ignores it.
    """

    def __init__(self, signals=SHUTDOWN_SIGNALS, poll_ms=DEFAULT_POLL_MS):
        if isinstance(signals, int):
            signals = (signals,)
        self.signals = tuple(signals)
        if not self.signals:
            raise ValueError("no signals given")
        if poll_ms < 1:
            raise ValueError("poll_ms must be at least 1")
        self.poll_ms = poll_ms
        self._source = _platform.SignalSource(self.signals)
        self._open = False

    def open(self):
        self._source.open()
        self._open = True
        return self

    def close(self):
        if self._open:
            self._source.close()
            self._open = False

    def pending(self):
        """Return a signal number already delivered, or None. Does not block."""
        return self._source.pending()

    async def wait(self):
        """Wait for one of the named signals and return its number."""
        if self._source.WAITABLE:
            return await self._source.wait()
        while True:
            signo = self._source.pending()
            if signo is not None:
                return signo
            await sleep_ms(self.poll_ms)

    async def __aenter__(self):
        return self.open()

    async def __aexit__(self, *args):
        self.close()


async def wait_for_signals(signals=SHUTDOWN_SIGNALS, poll_ms=DEFAULT_POLL_MS):
    """Wait for one signal out of a set and return its number.

    This opens and closes the watcher around the wait, so a signal that arrives
    before the call is not seen. Use SignalWatcher directly when a signal could
    arrive before the service is ready to wait for it.
    """
    async with SignalWatcher(signals, poll_ms=poll_ms) as watcher:
        return await watcher.wait()


async def wait_for_shutdown(poll_ms=DEFAULT_POLL_MS):
    """Wait for SIGTERM or SIGINT and return which one arrived."""
    return await wait_for_signals(SHUTDOWN_SIGNALS, poll_ms=poll_ms)
