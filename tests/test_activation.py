# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Socket activation tests, driven with real inherited descriptors.

No systemd is needed: the descriptors are pipes duplicated onto the numbers
systemd would have used.
"""

import harness as h

from mpsystemd import _platform, activation
from mpsystemd.errors import SystemdError
from mpsystemd.testing import install_listen_fds


def _clear():
    activation.unset_listen_environment()


class _Pipes:
    """Open n pipes, present their read ends as activated descriptors."""

    def __init__(self, count, names=None):
        self.count = count
        self.names = names
        self._owned = []
        self.installed = []

    def __enter__(self):
        reads = []
        for _ in range(self.count):
            read_fd, write_fd = _platform.pipe()
            self._owned.append(read_fd)
            self._owned.append(write_fd)
            reads.append(read_fd)
        self.installed = install_listen_fds(reads, self.names)
        return self

    def __exit__(self, *args):
        _clear()
        for fd in self.installed:
            try:
                _platform.close_fd(fd)
            except OSError:
                pass
        for fd in self._owned:
            try:
                _platform.close_fd(fd)
            except OSError:
                pass


def test_no_descriptors_without_the_environment():
    _clear()
    h.eq(activation.listen_fds(), 0)
    h.eq(activation.listen_fd_names(), [])
    h.eq(activation.listen_fds_with_names(), {})


def test_no_descriptors_when_listen_pid_names_another_process():
    with _Pipes(2):
        _platform.setenv("LISTEN_PID", str(_platform.getpid() + 1))
        h.eq(activation.listen_fds(), 0)


def test_no_descriptors_when_listen_pid_is_absent():
    with _Pipes(2):
        _platform.unsetenv("LISTEN_PID")
        h.eq(activation.listen_fds(), 0)


def test_no_descriptors_for_an_unparseable_count():
    with _Pipes(2):
        _platform.setenv("LISTEN_FDS", "two")
        h.eq(activation.listen_fds(), 0)


def test_count_matches_the_descriptors_passed():
    with _Pipes(3) as pipes:
        h.eq(activation.listen_fds(), 3)
        h.eq(pipes.installed, [3, 4, 5])


def test_first_descriptor_is_three():
    with _Pipes(1):
        h.eq(activation.LISTEN_FDS_START, 3)
        h.eq(activation.listen_fds_with_names(), {activation.UNNAMED: [3]})


def test_names_default_to_unknown():
    with _Pipes(2):
        h.eq(activation.listen_fd_names(), ["unknown", "unknown"])


def test_names_come_from_listen_fdnames_in_order():
    with _Pipes(2, names=["http", "control"]):
        h.eq(activation.listen_fd_names(), ["http", "control"])
        h.eq(
            activation.listen_fds_with_names(),
            {"http": [3], "control": [4]},
        )


def test_a_repeated_name_maps_to_several_descriptors():
    with _Pipes(3, names=["http", "http", "control"]):
        h.eq(
            activation.listen_fds_with_names(),
            {"http": [3, 4], "control": [5]},
        )


def test_a_name_count_mismatch_is_an_error():
    with _Pipes(3, names=["a", "b", "c"]):
        _platform.setenv("LISTEN_FDNAMES", "a:b")
        h.raises(SystemdError, activation.listen_fd_names)


def test_descriptors_are_marked_close_on_exec():
    with _Pipes(2) as pipes:
        for fd in pipes.installed:
            h.false(
                _platform.is_cloexec(fd),
                "descriptor %d started out close on exec" % fd,
            )
        h.eq(activation.listen_fds(), 2)
        for fd in pipes.installed:
            h.true(
                _platform.is_cloexec(fd),
                "descriptor %d was not marked close on exec" % fd,
            )


def test_cloexec_can_be_declined():
    with _Pipes(2) as pipes:
        h.eq(activation.listen_fds(cloexec=False), 2)
        for fd in pipes.installed:
            h.false(_platform.is_cloexec(fd))


def test_unset_environment_clears_all_three_variables():
    with _Pipes(2):
        h.eq(activation.listen_fds(unset_environment=True), 2)
        for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
            h.eq(_platform.getenv(name), None, name)
        h.eq(activation.listen_fds(), 0)


def test_the_activated_descriptors_are_the_ones_handed_in():
    """Writing into a pipe must be readable through the activated descriptor."""
    read_fd, write_fd = _platform.pipe()
    installed = []
    try:
        installed = install_listen_fds([read_fd], ["data"])
        h.eq(activation.listen_fds(), 1)
        mapping = activation.listen_fds_with_names()
        h.eq(mapping, {"data": [3]})
        _platform.write_fd(write_fd, b"payload")
        h.eq(_platform.read_fd(mapping["data"][0], 16), b"payload")
    finally:
        _clear()
        for fd in installed + [read_fd, write_fd]:
            try:
                _platform.close_fd(fd)
            except OSError:
                pass
