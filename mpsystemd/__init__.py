# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""systemd service integration for the MicroPython unix port and CPython.

This package speaks the systemd protocols directly over AF_UNIX sockets and the
LISTEN_* and WATCHDOG_* environment variables. It does not link libsystemd.

Importing this module keeps to the notification protocol, socket activation and
the small system checks. The asyncio helpers live in mpsystemd.watchdog and
mpsystemd.signals, the journal in mpsystemd.journal, and test helpers in
mpsystemd.testing, so that nothing pulls asyncio in unless it is used.
"""

from ._platform import MICROPYTHON, monotonic_us
from .activation import (
    LISTEN_FDS_START,
    listen_fd_names,
    listen_fds,
    listen_fds_with_names,
    unset_listen_environment,
)
from .errors import (
    AddressError,
    JournalError,
    SystemdError,
    UnsupportedError,
)
from .daemon import (
    Notifier,
    barrier,
    build_payload,
    errno,
    extend_timeout,
    fdstore,
    fdstore_remove,
    main_pid,
    notify,
    notify_available,
    ready,
    reloading,
    status,
    stopping,
    unset_notify_environment,
    watchdog_enabled,
    watchdog_ping,
    watchdog_trigger,
)
from .util import booted

__version__ = "0.1.0"

__all__ = [
    "AddressError",
    "JournalError",
    "LISTEN_FDS_START",
    "MICROPYTHON",
    "Notifier",
    "SystemdError",
    "UnsupportedError",
    "barrier",
    "booted",
    "build_payload",
    "errno",
    "extend_timeout",
    "fdstore",
    "fdstore_remove",
    "listen_fd_names",
    "listen_fds",
    "listen_fds_with_names",
    "main_pid",
    "monotonic_us",
    "notify",
    "notify_available",
    "ready",
    "reloading",
    "status",
    "stopping",
    "unset_listen_environment",
    "unset_notify_environment",
    "watchdog_enabled",
    "watchdog_ping",
    "watchdog_trigger",
    "__version__",
]
