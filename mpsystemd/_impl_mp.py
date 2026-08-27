# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""libc backed platform primitives for the MicroPython unix port.

The unix port exports socket.AF_UNIX as a constant but never builds a
sockaddr_un, so connect() and sendto() on a filesystem path fail with EINVAL.
Every AF_UNIX operation therefore goes through libc by way of ffi. Signals
arrive through signalfd(2) because the port has no signal module, and because an
ffi callback used as a signal handler would re-enter the interpreter from signal
context.
"""

import ffi
import os
import struct
import uctypes

from .errors import SystemdError, UnsupportedError

# Native word size. struct honours native alignment on this port, and the
# layouts below are expressed in words so that they hold on LP64 and ILP32.
WORD = struct.calcsize("P")

AF_UNIX = 1
SOCK_DGRAM = 2
SOCK_CLOEXEC = 0o2000000
SOL_SOCKET = 1
SCM_RIGHTS = 1
MSG_NOSIGNAL = 0x4000
MSG_CMSG_CLOEXEC = 0x40000000

O_CLOEXEC = 0o2000000
F_DUPFD = 0
F_GETFD = 1
F_SETFD = 2
FD_CLOEXEC = 1
SEEK_SET = 0
SEEK_END = 2
F_ADD_SEALS = 1033
F_SEAL_SEAL = 0x0001
F_SEAL_SHRINK = 0x0002
F_SEAL_GROW = 0x0004
F_SEAL_WRITE = 0x0008
MEMFD_SEALS = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE
MFD_CLOEXEC = 1
MFD_ALLOW_SEALING = 2

CLOCK_MONOTONIC = 1
# struct timespec is two native longs on the Linux ABIs the unix port targets.
_TIMESPEC_FORMAT = "ll"
_TIMESPEC_SIZE = struct.calcsize(_TIMESPEC_FORMAT)

SIG_BLOCK = 0
SIG_SETMASK = 2
# sizeof(sigset_t) on Linux, larger than any signal number in use.
SIGSET_SIZE = 128
# sizeof(struct signalfd_siginfo), whose first member is the uint32 ssi_signo.
SIGNALFD_SIGINFO_SIZE = 128
SFD_CLOEXEC = 0o2000000
SFD_NONBLOCK = 0o4000
_SSI_SIGNO_SIZE = struct.calcsize("I")
_INT_SIZE = struct.calcsize("i")

EINTR = 4
EAGAIN = 11

_USEC_PER_SEC = 1000000
_NSEC_PER_USEC = 1000

# dlopen(NULL) resolves against the global symbol namespace, which holds the
# libc the interpreter is already linked against. That works for glibc and musl
# alike without naming a shared object file.
_LIBC_CANDIDATES = (None, "libc.so.6", "libc.so")


def _open_libc():
    last = None
    for name in _LIBC_CANDIDATES:
        try:
            handle = ffi.open(name)
        except OSError as exc:
            last = exc
            continue
        try:
            handle.func("i", "getpid", "")
        except OSError as exc:
            last = exc
            continue
        return handle
    raise UnsupportedError("could not resolve libc through ffi: %s" % (last,))


_libc = _open_libc()


def _optional(restype, name, argtypes):
    """Bind a libc symbol, or return None when this libc lacks it."""
    try:
        return _libc.func(restype, name, argtypes)
    except OSError:
        return None


def _required(restype, name, argtypes):
    """Bind a libc symbol this package cannot work without."""
    bound = _optional(restype, name, argtypes)
    if bound is None:
        raise UnsupportedError("libc does not export %s" % name)
    return bound


_socket = _required("i", "socket", "iii")
_bind = _required("i", "bind", "ipI")
_sendto = _required("l", "sendto", "ipLipI")
_recv = _required("l", "recv", "ipLi")
_sendmsg = _required("l", "sendmsg", "ipi")
_recvmsg = _required("l", "recvmsg", "ipi")
_close = _required("i", "close", "i")
_read = _required("l", "read", "ipL")
_write = _required("l", "write", "ipL")
_pipe2 = _required("i", "pipe2", "pi")
_dup2 = _required("i", "dup2", "ii")
_fcntl = _required("i", "fcntl", "iii")
_lseek = _required("l", "lseek", "ili")
_getpid = _required("i", "getpid", "")
_kill = _required("i", "kill", "ii")
_clock_gettime = _required("i", "clock_gettime", "ip")
_strerror = _required("s", "strerror", "i")
_sigemptyset = _required("i", "sigemptyset", "p")
_sigaddset = _required("i", "sigaddset", "pi")
_sigprocmask = _required("i", "sigprocmask", "ipp")
_signalfd = _required("i", "signalfd", "ipi")
# glibc grew memfd_create in 2.27, so the journal falls back without it.
_memfd_create = _optional("i", "memfd_create", "pI")

try:
    _last_errno = os.errno
except AttributeError:
    _errno_location = _required("p", "__errno_location", "")

    def _last_errno():
        return struct.unpack(
            "i", uctypes.bytes_at(_errno_location(), _INT_SIZE)
        )[0]


def _oserror(err, call):
    return OSError(err, "%s: %s" % (call, _strerror(err)))


def _check(ret, call):
    """Raise OSError when a libc call reports failure, otherwise pass it on."""
    if ret < 0:
        err = _last_errno()
        raise _oserror(err, call)
    return ret


def _cstr(text):
    if isinstance(text, str):
        text = text.encode("utf-8")
    return text + b"\x00"


# --- environment -----------------------------------------------------------


def getenv(name):
    return os.getenv(name)


def setenv(name, value):
    os.putenv(name, value)


def unsetenv(name):
    os.unsetenv(name)


# --- process and clock -----------------------------------------------------


def getpid():
    return _getpid()


def monotonic_us():
    """CLOCK_MONOTONIC in microseconds, the base systemd compares against."""
    buf = bytearray(_TIMESPEC_SIZE)
    _check(_clock_gettime(CLOCK_MONOTONIC, buf), "clock_gettime")
    sec, nsec = struct.unpack(_TIMESPEC_FORMAT, buf)
    return sec * _USEC_PER_SEC + nsec // _NSEC_PER_USEC


def kill(pid, signo):
    _check(_kill(pid, signo), "kill")


# --- descriptors -----------------------------------------------------------


def pipe():
    buf = bytearray(2 * _INT_SIZE)
    _check(_pipe2(buf, O_CLOEXEC), "pipe2")
    return struct.unpack("ii", buf)


def close_fd(fd):
    _check(_close(fd), "close")


def read_fd(fd, size):
    buf = bytearray(size)
    got = _read(fd, buf, size)
    if got < 0:
        err = _last_errno()
        if err in (EAGAIN, EINTR):
            return None
        raise _oserror(err, "read")
    return bytes(buf[:got])


def write_fd(fd, data):
    return _check(_write(fd, data, len(data)), "write")


def dup2(oldfd, newfd):
    return _check(_dup2(oldfd, newfd), "dup2")


def set_cloexec(fd):
    _check(_fcntl(fd, F_SETFD, FD_CLOEXEC), "fcntl(F_SETFD)")


def clear_cloexec(fd):
    _check(_fcntl(fd, F_SETFD, 0), "fcntl(F_SETFD)")


def is_cloexec(fd):
    flags = _check(_fcntl(fd, F_GETFD, 0), "fcntl(F_GETFD)")
    return bool(flags & FD_CLOEXEC)


def dup_above(fd, minfd):
    """Duplicate fd onto the lowest free descriptor at or above minfd."""
    return _check(_fcntl(fd, F_DUPFD, minfd), "fcntl(F_DUPFD)")


def lseek(fd, offset, whence=SEEK_SET):
    return _check(_lseek(fd, offset, whence), "lseek")


def memfd_sealed(name, data):
    """Return a sealed read only memfd holding data.

    The journal accepts an entry as a sealed memfd when the datagram form is too
    large for the socket buffer.
    """
    if _memfd_create is None:
        raise UnsupportedError("memfd_create is not available in this libc")
    fd = _check(
        _memfd_create(_cstr(name), MFD_CLOEXEC | MFD_ALLOW_SEALING), "memfd_create"
    )
    try:
        written = 0
        view = data
        while written < len(data):
            if written:
                view = data[written:]
            written += _check(_write(fd, view, len(view)), "write")
        _check(_fcntl(fd, F_ADD_SEALS, MEMFD_SEALS), "fcntl(F_ADD_SEALS)")
        # Rewind so that a reader which uses the file offset, rather than mmap
        # the way journald does, sees the whole entry.
        _check(_lseek(fd, 0, SEEK_SET), "lseek")
    except BaseException:
        _close(fd)
        raise
    return fd


# --- AF_UNIX datagrams -----------------------------------------------------

# struct msghdr is seven native words wide on both the LP64 and the ILP32 Linux
# ABI: the 32 bit msg_namelen and msg_flags each take a full word once padding is
# applied. msg_flags is the last word and is always left zero here.
_MSGHDR_WORDS = 7
_OFF_NAME = 0
_OFF_NAMELEN = WORD
_OFF_IOV = 2 * WORD
_OFF_IOVLEN = 3 * WORD
_OFF_CONTROL = 4 * WORD
_OFF_CONTROLLEN = 5 * WORD
_MSGHDR_SIZE = _MSGHDR_WORDS * WORD

_IOVEC_SIZE = 2 * WORD
# SCM_RIGHTS is an array of ints.
_FD_SIZE = _INT_SIZE


def _cmsg_align(size):
    return (size + WORD - 1) & ~(WORD - 1)


# sizeof(struct cmsghdr) is a size_t plus cmsg_level and cmsg_type, aligned up to
# a word.
_CMSG_HDR_SIZE = _cmsg_align(WORD + 2 * _INT_SIZE)

# SCM_RIGHTS accepts at most this many descriptors in one message.
MAX_FDS_PER_MESSAGE = 253


_SA_FAMILY_FORMAT = "H"
_SA_FAMILY_SIZE = struct.calcsize(_SA_FAMILY_FORMAT)


def _sockaddr_un(sun_path):
    """Return a sockaddr_un buffer and the address length to report with it.

    The buffer keeps a trailing NUL so that it is a well formed sockaddr_un, but
    the reported length stops short of it, which is what keeps the NUL out of an
    abstract namespace name.
    """
    name = bytearray(
        struct.pack(_SA_FAMILY_FORMAT, AF_UNIX) + sun_path + b"\x00"
    )
    return name, _SA_FAMILY_SIZE + len(sun_path)


def _iovec(buf):
    iov = bytearray(_IOVEC_SIZE)
    struct.pack_into("P", iov, 0, uctypes.addressof(buf))
    struct.pack_into("L", iov, WORD, len(buf))
    return iov


def _msghdr(name, namelen, iov, control):
    mh = bytearray(_MSGHDR_SIZE)
    if name is not None:
        struct.pack_into("P", mh, _OFF_NAME, uctypes.addressof(name))
        struct.pack_into("I", mh, _OFF_NAMELEN, namelen)
    struct.pack_into("P", mh, _OFF_IOV, uctypes.addressof(iov))
    struct.pack_into("L", mh, _OFF_IOVLEN, 1)
    if control is not None:
        struct.pack_into("P", mh, _OFF_CONTROL, uctypes.addressof(control))
        struct.pack_into("L", mh, _OFF_CONTROLLEN, len(control))
    return mh


def _scm_rights(fds):
    control = bytearray(_CMSG_HDR_SIZE + _cmsg_align(_FD_SIZE * len(fds)))
    struct.pack_into("L", control, 0, _CMSG_HDR_SIZE + _FD_SIZE * len(fds))
    struct.pack_into("ii", control, WORD, SOL_SOCKET, SCM_RIGHTS)
    struct.pack_into("i" * len(fds), control, _CMSG_HDR_SIZE, *fds)
    return control


def _parse_scm_rights(control, controllen):
    if controllen < _CMSG_HDR_SIZE:
        return []
    cmsg_len = struct.unpack_from("L", control, 0)[0]
    level, kind = struct.unpack_from("ii", control, WORD)
    if level != SOL_SOCKET or kind != SCM_RIGHTS:
        return []
    count = (min(cmsg_len, controllen) - _CMSG_HDR_SIZE) // _FD_SIZE
    if count <= 0:
        return []
    return list(struct.unpack_from("i" * count, control, _CMSG_HDR_SIZE))


class UnixDatagram:
    """An AF_UNIX SOCK_DGRAM socket driven through libc."""

    def __init__(self, peer=None):
        self._fd = _check(_socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0), "socket")
        self._name = None
        self._namelen = 0
        if peer is not None:
            self.set_peer(peer)

    def set_peer(self, sun_path):
        self._name, self._namelen = _sockaddr_un(sun_path)

    def fileno(self):
        return self._fd

    def bind(self, sun_path):
        name, namelen = _sockaddr_un(sun_path)
        _check(_bind(self._fd, name, namelen), "bind")

    def send(self, data):
        if self._name is None:
            raise SystemdError("no peer address set on this socket")
        return _check(
            _sendto(self._fd, data, len(data), MSG_NOSIGNAL, self._name, self._namelen),
            "sendto",
        )

    def send_with_fds(self, data, fds):
        if self._name is None:
            raise SystemdError("no peer address set on this socket")
        if not fds:
            return self.send(data)
        if len(fds) > MAX_FDS_PER_MESSAGE:
            raise ValueError(
                "at most %d descriptors fit in one message" % MAX_FDS_PER_MESSAGE
            )
        payload = bytearray(data)
        mh = _msghdr(self._name, self._namelen, _iovec(payload), _scm_rights(fds))
        return _check(_sendmsg(self._fd, mh, MSG_NOSIGNAL), "sendmsg")

    def recv(self, bufsize=8192, max_fds=0):
        """Receive one datagram, returning (data, fds). Blocks."""
        if not max_fds:
            buf = bytearray(bufsize)
            got = _check(_recv(self._fd, buf, bufsize, 0), "recv")
            return bytes(buf[:got]), []
        buf = bytearray(bufsize)
        control = bytearray(_CMSG_HDR_SIZE + _cmsg_align(_FD_SIZE * max_fds))
        mh = _msghdr(None, 0, _iovec(buf), control)
        got = _check(_recvmsg(self._fd, mh, MSG_CMSG_CLOEXEC), "recvmsg")
        controllen = struct.unpack_from("L", mh, _OFF_CONTROLLEN)[0]
        return bytes(buf[:got]), _parse_scm_rights(control, controllen)

    def close(self):
        if self._fd >= 0:
            _close(self._fd)
            self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# --- signals ---------------------------------------------------------------


class SignalSource:
    """Signal delivery through signalfd(2).

    open() blocks the requested signals for the calling thread first, so their
    default dispositions never run. close() restores the previous mask, at which
    point any signal that arrived and was not consumed is delivered normally.
    """

    WAITABLE = False

    def __init__(self, signals):
        self._signals = tuple(signals)
        self._fd = -1
        self._saved = None
        self._buf = bytearray(SIGNALFD_SIGINFO_SIZE)

    def open(self):
        mask = bytearray(SIGSET_SIZE)
        _check(_sigemptyset(mask), "sigemptyset")
        for signo in self._signals:
            _check(_sigaddset(mask, signo), "sigaddset")
        saved = bytearray(SIGSET_SIZE)
        _check(_sigprocmask(SIG_BLOCK, mask, saved), "sigprocmask")
        self._saved = saved
        try:
            self._fd = _check(
                _signalfd(-1, mask, SFD_CLOEXEC | SFD_NONBLOCK), "signalfd"
            )
        except OSError:
            _sigprocmask(SIG_SETMASK, saved, None)
            self._saved = None
            raise

    def fileno(self):
        return self._fd

    def pending(self):
        if self._fd < 0:
            raise SystemdError("signal source is not open")
        got = _read(self._fd, self._buf, SIGNALFD_SIGINFO_SIZE)
        if got < 0:
            err = _last_errno()
            if err in (EAGAIN, EINTR):
                return None
            raise _oserror(err, "read")
        if got < _SSI_SIGNO_SIZE:
            return None
        return struct.unpack_from("I", self._buf, 0)[0]

    def close(self):
        if self._fd >= 0:
            _close(self._fd)
            self._fd = -1
        if self._saved is not None:
            _sigprocmask(SIG_SETMASK, self._saved, None)
            self._saved = None
