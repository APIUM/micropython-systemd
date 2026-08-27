# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""The sd_notify service notification protocol.

A service inherits NOTIFY_SOCKET from systemd and sends newline separated
NAME=value assignments to it as AF_UNIX datagrams. Any number of assignments may
share one datagram.
"""

from . import _platform
from ._address import encode_unix_address
from .errors import AddressError

NOTIFY_SOCKET_ENV = "NOTIFY_SOCKET"
WATCHDOG_USEC_ENV = "WATCHDOG_USEC"
WATCHDOG_PID_ENV = "WATCHDOG_PID"

DEFAULT_BARRIER_TIMEOUT_MS = 5000

# systemd rejects a descriptor store name holding a colon or a control
# character, and caps it at this length.
FDNAME_MAX = 255

_NAME_FIRST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_NAME_REST = _NAME_FIRST + "0123456789_"

_ASCII_SPACE = 0x20
_ASCII_DELETE = 0x7F


def _format_value(value):
    if value is True:
        return "1"
    if value is False:
        return "0"
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    return str(value)


def _check_name(name):
    upper = name.upper()
    if not upper:
        raise ValueError("state field name is empty")
    if upper[0] not in _NAME_FIRST:
        raise ValueError("state field name must start with a letter: %r" % (name,))
    for char in upper:
        if char not in _NAME_REST:
            raise ValueError("state field name has an invalid character: %r" % (name,))
    return upper


def build_payload(states=(), fields=None):
    """Serialise raw state strings and keyword fields into one datagram body.

    Field names are upper cased and emitted in sorted order so that the result
    does not depend on dictionary ordering, which differs between the runtimes.
    """
    lines = []
    for state in states:
        if isinstance(state, (bytes, bytearray)):
            state = bytes(state).decode("utf-8")
        state = state.strip("\n")
        if state:
            lines.append(state)
    if fields:
        for name in sorted(fields.keys()):
            upper = _check_name(name)
            value = _format_value(fields[name])
            if "\n" in value:
                raise ValueError(
                    "state field %s holds a newline, which would start a new "
                    "assignment" % upper
                )
            lines.append("%s=%s" % (upper, value))
    if not lines:
        raise ValueError("nothing to notify")
    return ("\n".join(lines) + "\n").encode("utf-8")


def check_fdname(name):
    """Validate a descriptor store name against systemd's rules."""
    if not name:
        raise ValueError("descriptor store name is empty")
    if len(name) > FDNAME_MAX:
        raise ValueError(
            "descriptor store name is longer than %d characters" % FDNAME_MAX
        )
    for char in name:
        code = ord(char)
        if char == ":" or code < _ASCII_SPACE or code == _ASCII_DELETE:
            raise ValueError("descriptor store name has an invalid character")
    return name


class Notifier:
    """A notification socket held open across many messages.

    Module level functions in this package open and close a socket per message,
    matching sd_notify. Use this class instead when notifying often, for example
    from a watchdog loop.
    """

    def __init__(self, address=None):
        if address is None:
            address = _platform.getenv(NOTIFY_SOCKET_ENV)
        self.address = address or None
        self._sun_path = None
        self._sock = None
        if self.address:
            self._sun_path = encode_unix_address(self.address)

    @property
    def available(self):
        """True when a usable NOTIFY_SOCKET address is known."""
        return self._sun_path is not None

    def send_raw(self, payload, fds=None):
        """Send one datagram. Returns False when no notification socket is set.

        Raises OSError when a socket address is set but the message cannot be
        delivered, since that is a real failure rather than the ordinary case of
        running outside systemd.
        """
        if self._sun_path is None:
            return False
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if self._sock is None:
            self._sock = _platform.UnixDatagram(peer=self._sun_path)
        if fds:
            self._sock.send_with_fds(payload, fds)
        else:
            self._sock.send(payload)
        return True

    def notify(self, *states, **fields):
        """Send any mix of raw state strings and NAME=value keyword fields."""
        return self.send_raw(build_payload(states, fields))

    def ready(self, status=None):
        if status is None:
            return self.notify(READY=1)
        return self.notify(READY=1, STATUS=status)

    def stopping(self, status=None):
        if status is None:
            return self.notify(STOPPING=1)
        return self.notify(STOPPING=1, STATUS=status)

    def reloading(self, status=None):
        """Announce a reload.

        The protocol requires MONOTONIC_USEC alongside RELOADING=1 so that
        systemd can tell a reload that started before its own request from one
        that started after, so it is filled in here.
        """
        fields = {"RELOADING": 1, "MONOTONIC_USEC": _platform.monotonic_us()}
        if status is not None:
            fields["STATUS"] = status
        return self.notify(**fields)

    def status(self, text):
        return self.notify(STATUS=text)

    def errno(self, value):
        return self.notify(ERRNO=int(value))

    def main_pid(self, pid=None):
        if pid is None:
            pid = _platform.getpid()
        return self.notify(MAINPID=int(pid))

    def extend_timeout(self, usec):
        """Ask for usec more before the current start, stop or reload times out."""
        return self.notify(EXTEND_TIMEOUT_USEC=int(usec))

    def watchdog_ping(self):
        return self.notify(WATCHDOG=1)

    def watchdog_trigger(self):
        """Report failure at once instead of waiting for the watchdog to lapse."""
        return self.notify(WATCHDOG="trigger")

    def fdstore(self, fds, name=None, poll=None):
        """Hand descriptors to systemd to hold across a restart of this service.

        poll=False asks systemd not to watch the descriptors for POLLHUP or
        POLLERR, which it otherwise does and treats as a reason to restart.
        """
        if isinstance(fds, int):
            fds = (fds,)
        fds = tuple(fds)
        if not fds:
            raise ValueError("no descriptors given")
        fields = {"FDSTORE": 1}
        if name is not None:
            fields["FDNAME"] = check_fdname(name)
        if poll is not None:
            fields["FDPOLL"] = bool(poll)
        return self.send_raw(build_payload((), fields), fds)

    def fdstore_remove(self, name):
        """Drop descriptors previously stored under name."""
        return self.notify(FDSTOREREMOVE=1, FDNAME=check_fdname(name))

    def barrier_begin(self):
        """Send BARRIER=1 with a pipe write end and return the read end.

        The caller owns the returned descriptor and must close it. Returns None
        when there is no notification socket.
        """
        if self._sun_path is None:
            return None
        read_fd, write_fd = _platform.pipe()
        try:
            # BARRIER=1 has to be the only assignment in its datagram.
            self.send_raw(b"BARRIER=1\n", (write_fd,))
        except BaseException:
            _platform.close_fd(read_fd)
            raise
        finally:
            _platform.close_fd(write_fd)
        return read_fd

    def barrier_finish(self, read_fd, timeout_ms=DEFAULT_BARRIER_TIMEOUT_MS):
        """Wait for systemd to close its copy of the barrier descriptor.

        End of file on the read end means every notification sent before the
        barrier has been processed. Returns False on timeout.
        """
        events = _platform.poll_fd(
            read_fd, _platform.POLLIN | _platform.POLLHUP, timeout_ms
        )
        return bool(events)

    def barrier(self, timeout_ms=DEFAULT_BARRIER_TIMEOUT_MS):
        """Block until systemd has processed every earlier notification.

        Returns False on timeout or with no notification socket.
        """
        read_fd = self.barrier_begin()
        if read_fd is None:
            return False
        try:
            return self.barrier_finish(read_fd, timeout_ms)
        finally:
            _platform.close_fd(read_fd)

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# --- module level convenience ----------------------------------------------


def notify_available():
    """True when NOTIFY_SOCKET names a socket address this package can use."""
    address = _platform.getenv(NOTIFY_SOCKET_ENV)
    if not address:
        return False
    try:
        encode_unix_address(address)
    except AddressError:
        return False
    return True


def unset_notify_environment():
    """Remove NOTIFY_SOCKET so that child processes do not inherit it."""
    _platform.unsetenv(NOTIFY_SOCKET_ENV)


def notify(*states, **fields):
    with Notifier() as notifier:
        return notifier.notify(*states, **fields)


def ready(status=None):
    with Notifier() as notifier:
        return notifier.ready(status)


def stopping(status=None):
    with Notifier() as notifier:
        return notifier.stopping(status)


def reloading(status=None):
    with Notifier() as notifier:
        return notifier.reloading(status)


def status(text):
    with Notifier() as notifier:
        return notifier.status(text)


def errno(value):
    with Notifier() as notifier:
        return notifier.errno(value)


def main_pid(pid=None):
    with Notifier() as notifier:
        return notifier.main_pid(pid)


def extend_timeout(usec):
    with Notifier() as notifier:
        return notifier.extend_timeout(usec)


def watchdog_ping():
    with Notifier() as notifier:
        return notifier.watchdog_ping()


def watchdog_trigger():
    with Notifier() as notifier:
        return notifier.watchdog_trigger()


def fdstore(fds, name=None, poll=None):
    with Notifier() as notifier:
        return notifier.fdstore(fds, name=name, poll=poll)


def fdstore_remove(name):
    with Notifier() as notifier:
        return notifier.fdstore_remove(name)


def barrier(timeout_ms=DEFAULT_BARRIER_TIMEOUT_MS):
    with Notifier() as notifier:
        return notifier.barrier(timeout_ms)


def watchdog_enabled(unset_environment=False):
    """Return the watchdog interval in microseconds, or 0 when there is none.

    WATCHDOG_PID names the process the interval belongs to. A child that
    inherited the environment must not service its parent's watchdog, so a
    mismatch reports no watchdog.
    """
    usec_text = _platform.getenv(WATCHDOG_USEC_ENV)
    pid_text = _platform.getenv(WATCHDOG_PID_ENV)
    if unset_environment:
        _platform.unsetenv(WATCHDOG_USEC_ENV)
        _platform.unsetenv(WATCHDOG_PID_ENV)
    if not usec_text:
        return 0
    try:
        usec = int(usec_text)
    except ValueError:
        return 0
    if usec <= 0:
        return 0
    if pid_text:
        try:
            pid = int(pid_text)
        except ValueError:
            return 0
        if pid != _platform.getpid():
            return 0
    return usec
