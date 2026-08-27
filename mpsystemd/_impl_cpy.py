# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Standard library platform primitives for CPython on Linux.

CPython implements sockaddr_un and has a signal module, so this backend uses
socket and the running event loop's signal handling instead of libc calls.
"""

import asyncio
import fcntl
import os
import socket
import struct
import time

from .errors import SystemdError, UnsupportedError

F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
MEMFD_SEALS = (
    getattr(fcntl, "F_SEAL_SEAL", 0x0001)
    | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
    | getattr(fcntl, "F_SEAL_GROW", 0x0004)
    | getattr(fcntl, "F_SEAL_WRITE", 0x0008)
)

# SCM_RIGHTS accepts at most this many descriptors in one message.
MAX_FDS_PER_MESSAGE = 253

SEEK_SET = os.SEEK_SET
SEEK_END = os.SEEK_END


# --- environment -----------------------------------------------------------


def getenv(name):
    return os.environ.get(name)


def setenv(name, value):
    os.environ[name] = value


def unsetenv(name):
    os.environ.pop(name, None)


# --- process and clock -----------------------------------------------------


def getpid():
    return os.getpid()


def monotonic_us():
    """CLOCK_MONOTONIC in microseconds, the base systemd compares against."""
    return time.monotonic_ns() // 1000


def kill(pid, signo):
    os.kill(pid, signo)


# --- descriptors -----------------------------------------------------------


def pipe():
    return os.pipe()


def close_fd(fd):
    os.close(fd)


def read_fd(fd, size):
    try:
        return os.read(fd, size)
    except BlockingIOError:
        return None
    except InterruptedError:
        return None


def write_fd(fd, data):
    return os.write(fd, data)


def dup2(oldfd, newfd):
    return os.dup2(oldfd, newfd)


def set_cloexec(fd):
    os.set_inheritable(fd, False)


def clear_cloexec(fd):
    os.set_inheritable(fd, True)


def is_cloexec(fd):
    return not os.get_inheritable(fd)


def dup_above(fd, minfd):
    """Duplicate fd onto the lowest free descriptor at or above minfd."""
    return fcntl.fcntl(fd, fcntl.F_DUPFD, minfd)


def lseek(fd, offset, whence=os.SEEK_SET):
    return os.lseek(fd, offset, whence)


def memfd_sealed(name, data):
    """Return a sealed read only memfd holding data."""
    if not hasattr(os, "memfd_create"):
        raise UnsupportedError("os.memfd_create is not available")
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        fcntl.fcntl(fd, F_ADD_SEALS, MEMFD_SEALS)
        # Rewind so that a reader which uses the file offset, rather than mmap
        # the way journald does, sees the whole entry.
        os.lseek(fd, 0, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise
    return fd


# --- AF_UNIX datagrams -----------------------------------------------------


class UnixDatagram:
    """An AF_UNIX SOCK_DGRAM socket built on the standard socket module."""

    def __init__(self, peer=None):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._peer = None
        if peer is not None:
            self.set_peer(peer)

    def set_peer(self, sun_path):
        # CPython takes raw bytes for an AF_UNIX address and treats a leading
        # NUL as selecting the abstract namespace, matching sun_path exactly.
        self._peer = bytes(sun_path)

    def fileno(self):
        return self._sock.fileno()

    def bind(self, sun_path):
        self._sock.bind(bytes(sun_path))

    def send(self, data):
        if self._peer is None:
            raise SystemdError("no peer address set on this socket")
        return self._sock.sendto(data, self._peer)

    def send_with_fds(self, data, fds):
        if self._peer is None:
            raise SystemdError("no peer address set on this socket")
        if not fds:
            return self.send(data)
        if len(fds) > MAX_FDS_PER_MESSAGE:
            raise ValueError(
                "at most %d descriptors fit in one message" % MAX_FDS_PER_MESSAGE
            )
        rights = struct.pack("%di" % len(fds), *fds)
        return self._sock.sendmsg(
            [data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)], 0, self._peer
        )

    def recv(self, bufsize=8192, max_fds=0):
        """Receive one datagram, returning (data, fds). Blocks."""
        if not max_fds:
            return self._sock.recv(bufsize), []
        ancsize = socket.CMSG_SPACE(max_fds * struct.calcsize("i"))
        data, ancdata, _flags, _addr = self._sock.recvmsg(
            bufsize, ancsize, socket.MSG_CMSG_CLOEXEC
        )
        fds = []
        fdsize = struct.calcsize("i")
        for level, kind, payload in ancdata:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                count = len(payload) // fdsize
                fds.extend(struct.unpack("%di" % count, payload[: count * fdsize]))
        return data, fds

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# --- signals ---------------------------------------------------------------


class SignalSource:
    """Signal delivery through the running event loop.

    loop.add_signal_handler wakes the selector directly, so there is nothing to
    poll and the caller's poll interval is ignored on this runtime.
    """

    WAITABLE = True

    def __init__(self, signals):
        self._signals = tuple(signals)
        self._queue = []
        self._event = None
        self._installed = []
        self._loop = None

    def open(self):
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            raise UnsupportedError(
                "signal watching needs a running event loop on CPython"
            )
        self._event = asyncio.Event()
        for signo in self._signals:
            self._loop.add_signal_handler(signo, self._deliver, signo)
            self._installed.append(signo)

    def _deliver(self, signo):
        self._queue.append(signo)
        self._event.set()

    def fileno(self):
        return -1

    def pending(self):
        if self._loop is None:
            raise SystemdError("signal source is not open")
        if not self._queue:
            return None
        signo = self._queue.pop(0)
        if not self._queue:
            self._event.clear()
        return signo

    async def wait(self):
        while True:
            signo = self.pending()
            if signo is not None:
                return signo
            # Clearing before waiting means a set flag with an empty queue costs
            # one more wait rather than spinning. No handler can run between the
            # two calls, since they are dispatched by this same loop.
            self._event.clear()
            await self._event.wait()

    def close(self):
        while self._installed:
            signo = self._installed.pop()
            try:
                self._loop.remove_signal_handler(signo)
            except (RuntimeError, ValueError):
                pass
        self._loop = None
        self._queue = []
