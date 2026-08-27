# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Helpers for testing a service's systemd integration without systemd.

The MicroPython unix port cannot bind an AF_UNIX socket through its own socket
module, so a fake listener has to go through this package's libc backend. That
makes it worth shipping rather than leaving to each test suite.
"""

import binascii
import os

from . import _platform
from ._address import encode_unix_address
from .activation import (
    LISTEN_FDNAMES_ENV,
    LISTEN_FDS_ENV,
    LISTEN_FDS_START,
    LISTEN_PID_ENV,
)

_NAME_RANDOM_BYTES = 6


class Datagram:
    """One received datagram and any descriptors that came with it."""

    def __init__(self, data, fds):
        self.data = data
        self.fds = fds

    def text(self):
        return self.data.decode("utf-8")

    def lines(self):
        return [line for line in self.text().split("\n") if line]

    def fields(self):
        """Parse NAME=value lines into a mapping.

        A name repeated in one datagram keeps its last value, matching how
        systemd reads the notification protocol.
        """
        parsed = {}
        for line in self.lines():
            if "=" in line:
                name, value = line.split("=", 1)
                parsed[name] = value
            else:
                parsed[line] = ""
        return parsed

    def close_fds(self):
        while self.fds:
            _platform.close_fd(self.fds.pop())

    def __repr__(self):
        return "Datagram(%r, fds=%r)" % (self.data, self.fds)


class FakeNotifyListener:
    """A bound datagram socket that records what a service notifies.

    It defaults to the Linux abstract namespace, which needs no filesystem
    cleanup and cannot run into the 108 byte sun_path limit through a long
    temporary directory name.
    """

    def __init__(self, address=None, max_fds=8, bufsize=65536):
        if address is None:
            suffix = binascii.hexlify(os.urandom(_NAME_RANDOM_BYTES)).decode("ascii")
            address = "@mpsystemd-test-%s" % suffix
        self.address = address
        self.max_fds = max_fds
        self.bufsize = bufsize
        self._sock = _platform.UnixDatagram()
        self._sock.bind(encode_unix_address(address))

    def fileno(self):
        return self._sock.fileno()

    def receive(self, timeout_ms=2000):
        """Return the next Datagram, or None if none arrives in time."""
        events = _platform.poll_fd(
            self._sock.fileno(), _platform.POLLIN, timeout_ms
        )
        if not events:
            return None
        data, fds = self._sock.recv(self.bufsize, self.max_fds)
        return Datagram(data, fds)

    def receive_all(self, timeout_ms=200):
        """Drain every datagram already queued, waiting timeout_ms for the first."""
        received = []
        while True:
            datagram = self.receive(timeout_ms if not received else 0)
            if datagram is None:
                return received
            received.append(datagram)

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def install_listen_fds(fds, names=None, start=LISTEN_FDS_START):
    """Present fds as socket activation descriptors to this process.

    Each descriptor is duplicated onto start, start + 1 and so on, and the
    LISTEN_* variables are set to match. Any descriptor already open at one of
    those numbers is closed by the duplication, which is also what happens when
    systemd sets up a service.

    The sources are copied above the target range first, so an input descriptor
    that already sits inside the target range is not lost part way through.
    systemd passes descriptors without the close on exec flag, so it is cleared
    here too.
    """
    fds = list(fds)
    if names is not None and len(names) != len(fds):
        raise ValueError("names must line up with fds")
    scratch = [_platform.dup_above(fd, start + len(fds)) for fd in fds]
    try:
        for offset, fd in enumerate(scratch):
            _platform.dup2(fd, start + offset)
            _platform.clear_cloexec(start + offset)
    finally:
        for fd in scratch:
            _platform.close_fd(fd)
    _platform.setenv(LISTEN_PID_ENV, str(_platform.getpid()))
    _platform.setenv(LISTEN_FDS_ENV, str(len(fds)))
    if names is None:
        _platform.unsetenv(LISTEN_FDNAMES_ENV)
    else:
        _platform.setenv(LISTEN_FDNAMES_ENV, ":".join(names))
    return list(range(start, start + len(fds)))
