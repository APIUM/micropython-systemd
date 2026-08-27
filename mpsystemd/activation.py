# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Socket activation.

systemd passes listening sockets to a service as inherited descriptors starting
at 3, with LISTEN_FDS holding the count, LISTEN_PID naming the process the
descriptors were meant for, and LISTEN_FDNAMES holding colon separated names.
"""

from . import _platform
from .errors import SystemdError

LISTEN_PID_ENV = "LISTEN_PID"
LISTEN_FDS_ENV = "LISTEN_FDS"
LISTEN_FDNAMES_ENV = "LISTEN_FDNAMES"

# The first descriptor systemd hands over. 0, 1 and 2 stay as standard streams.
LISTEN_FDS_START = 3

# The name systemd reports for a descriptor with no name of its own.
UNNAMED = "unknown"

_FDNAMES_SEPARATOR = ":"


def _take_environment(unset_environment):
    pid_text = _platform.getenv(LISTEN_PID_ENV)
    count_text = _platform.getenv(LISTEN_FDS_ENV)
    names_text = _platform.getenv(LISTEN_FDNAMES_ENV)
    if unset_environment:
        _platform.unsetenv(LISTEN_PID_ENV)
        _platform.unsetenv(LISTEN_FDS_ENV)
        _platform.unsetenv(LISTEN_FDNAMES_ENV)
    return pid_text, count_text, names_text


def _count(pid_text, count_text):
    if not pid_text or not count_text:
        return 0
    try:
        pid = int(pid_text)
        count = int(count_text)
    except ValueError:
        return 0
    if pid != _platform.getpid():
        return 0
    if count <= 0:
        return 0
    return count


def listen_fds(unset_environment=False, cloexec=True):
    """Return how many descriptors systemd passed to this process.

    The descriptors are LISTEN_FDS_START through LISTEN_FDS_START + count - 1.
    cloexec marks each one close on exec, which is what sd_listen_fds does, so
    that a child process does not inherit a listening socket by accident.
    """
    pid_text, count_text, _names = _take_environment(unset_environment)
    count = _count(pid_text, count_text)
    if count and cloexec:
        for fd in range(LISTEN_FDS_START, LISTEN_FDS_START + count):
            _platform.set_cloexec(fd)
    return count


def listen_fd_names(unset_environment=False, cloexec=True):
    """Return one name per passed descriptor, in descriptor order.

    Descriptors with no name of their own report UNNAMED, matching what systemd
    reports when LISTEN_FDNAMES is absent.
    """
    pid_text, count_text, names_text = _take_environment(unset_environment)
    count = _count(pid_text, count_text)
    if not count:
        return []
    if cloexec:
        for fd in range(LISTEN_FDS_START, LISTEN_FDS_START + count):
            _platform.set_cloexec(fd)
    if not names_text:
        return [UNNAMED] * count
    names = names_text.split(_FDNAMES_SEPARATOR)
    if len(names) != count:
        raise SystemdError(
            "%s lists %d names for %d descriptors"
            % (LISTEN_FDNAMES_ENV, len(names), count)
        )
    return [name or UNNAMED for name in names]


def listen_fds_with_names(unset_environment=False, cloexec=True):
    """Return a mapping of name to the descriptors that have that name.

    A name may appear on more than one descriptor, because several sockets in one
    .socket unit can share a FileDescriptorName, so each value is a list.
    """
    names = listen_fd_names(unset_environment=unset_environment, cloexec=cloexec)
    mapping = {}
    for offset, name in enumerate(names):
        mapping.setdefault(name, []).append(LISTEN_FDS_START + offset)
    return mapping


def unset_listen_environment():
    """Remove the LISTEN_* variables so that child processes do not inherit them."""
    _platform.unsetenv(LISTEN_PID_ENV)
    _platform.unsetenv(LISTEN_FDS_ENV)
    _platform.unsetenv(LISTEN_FDNAMES_ENV)
