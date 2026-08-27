# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Runtime selection for the platform primitives the rest of the package uses.

The ffi module is present on the MicroPython unix port and absent from CPython,
so importing it is the test that picks a backend.
"""

import select

try:
    import ffi as _ffi
except ImportError:
    _ffi = None

MICROPYTHON = _ffi is not None

if MICROPYTHON:
    from . import _impl_mp as _impl
else:
    from . import _impl_cpy as _impl

getenv = _impl.getenv
setenv = _impl.setenv
unsetenv = _impl.unsetenv
getpid = _impl.getpid
monotonic_us = _impl.monotonic_us
kill = _impl.kill
pipe = _impl.pipe
close_fd = _impl.close_fd
read_fd = _impl.read_fd
write_fd = _impl.write_fd
dup2 = _impl.dup2
set_cloexec = _impl.set_cloexec
clear_cloexec = _impl.clear_cloexec
is_cloexec = _impl.is_cloexec
dup_above = _impl.dup_above
lseek = _impl.lseek
SEEK_SET = _impl.SEEK_SET
SEEK_END = _impl.SEEK_END
memfd_sealed = _impl.memfd_sealed
UnixDatagram = _impl.UnixDatagram
SignalSource = _impl.SignalSource
MAX_FDS_PER_MESSAGE = _impl.MAX_FDS_PER_MESSAGE

POLLIN = select.POLLIN
POLLHUP = select.POLLHUP
POLLERR = select.POLLERR


def poll_fd(fd, mask=POLLIN, timeout_ms=0):
    """Return the poll event mask for one descriptor, or 0 when it times out.

    select.poll has the same shape on both runtimes, so this is shared.
    """
    poller = select.poll()
    poller.register(fd, mask)
    try:
        events = poller.poll(timeout_ms)
    finally:
        poller.unregister(fd)
    if not events:
        return 0
    return events[0][1]
