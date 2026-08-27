# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Descriptor store and barrier tests, both of which pass descriptors over
SCM_RIGHTS. No systemd needed: a fake listener stands in for it.
"""

import harness as h

from mpsystemd import _platform
from mpsystemd.daemon import Notifier, check_fdname
from mpsystemd.testing import FakeNotifyListener


def _pipe():
    return _platform.pipe()


def test_a_stored_descriptor_arrives_with_the_message():
    read_fd, write_fd = _pipe()
    try:
        with FakeNotifyListener() as listener:
            with Notifier(listener.address) as notifier:
                h.true(notifier.fdstore(read_fd, name="state"))
            datagram = listener.receive(1000)
            h.ne(datagram, None, "no message arrived")
            h.eq(datagram.fields(), {"FDSTORE": "1", "FDNAME": "state"})
            h.eq(len(datagram.fds), 1, "no descriptor came with the message")
            try:
                # The descriptor must be the same pipe, not a copy of something
                # else, so data written here has to come out of it.
                _platform.write_fd(write_fd, b"kept")
                h.eq(_platform.read_fd(datagram.fds[0], 8), b"kept")
            finally:
                datagram.close_fds()
    finally:
        for fd in (read_fd, write_fd):
            try:
                _platform.close_fd(fd)
            except OSError:
                pass


def test_several_descriptors_arrive_together():
    pipes = [_pipe(), _pipe()]
    reads = [pair[0] for pair in pipes]
    try:
        with FakeNotifyListener() as listener:
            with Notifier(listener.address) as notifier:
                notifier.fdstore(reads, name="pair")
            datagram = listener.receive(1000)
            h.eq(len(datagram.fds), 2)
            datagram.close_fds()
    finally:
        for pair in pipes:
            for fd in pair:
                try:
                    _platform.close_fd(fd)
                except OSError:
                    pass


def test_fdstore_can_switch_polling_off():
    read_fd, write_fd = _pipe()
    try:
        with FakeNotifyListener() as listener:
            with Notifier(listener.address) as notifier:
                notifier.fdstore(read_fd, name="state", poll=False)
            datagram = listener.receive(1000)
            h.eq(
                datagram.fields(),
                {"FDSTORE": "1", "FDNAME": "state", "FDPOLL": "0"},
            )
            datagram.close_fds()
    finally:
        for fd in (read_fd, write_fd):
            _platform.close_fd(fd)


def test_fdstore_without_descriptors_is_an_error():
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            h.raises(ValueError, notifier.fdstore, ())


def test_more_descriptors_than_one_message_holds_is_an_error():
    too_many = list(range(_platform.MAX_FDS_PER_MESSAGE + 1))
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            h.raises(ValueError, notifier.fdstore, too_many)


def test_a_name_with_a_colon_is_rejected():
    h.raises(ValueError, check_fdname, "a:b")


def test_a_name_with_a_control_character_is_rejected():
    h.raises(ValueError, check_fdname, "a\tb")


def test_an_empty_name_is_rejected():
    h.raises(ValueError, check_fdname, "")


def test_an_overlong_name_is_rejected():
    check_fdname("n" * 255)
    h.raises(ValueError, check_fdname, "n" * 256)


def test_removal_names_the_entry_to_drop():
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            h.true(notifier.fdstore_remove("state"))
        h.eq(
            listener.receive(1000).fields(),
            {"FDSTOREREMOVE": "1", "FDNAME": "state"},
        )


# --- barrier ---------------------------------------------------------------


def test_a_barrier_completes_when_the_peer_closes_its_copy():
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            read_fd = notifier.barrier_begin()
            h.ne(read_fd, None)
            try:
                datagram = listener.receive(1000)
                h.ne(datagram, None, "no barrier message arrived")
                h.eq(datagram.fields(), {"BARRIER": "1"})
                h.eq(len(datagram.fds), 1, "no descriptor came with the barrier")
                h.false(
                    notifier.barrier_finish(read_fd, 0),
                    "the barrier completed before the peer let go",
                )
                datagram.close_fds()
                h.true(
                    notifier.barrier_finish(read_fd, 1000),
                    "the barrier did not complete after the peer let go",
                )
            finally:
                _platform.close_fd(read_fd)


def test_a_barrier_times_out_while_the_peer_holds_on():
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            h.false(notifier.barrier(timeout_ms=100))
        # The undrained datagram still holds the descriptor, which is why the
        # barrier could not complete.
        datagram = listener.receive(200)
        h.eq(len(datagram.fds), 1)
        datagram.close_fds()


def test_a_barrier_without_a_notification_socket_is_falsey():
    _platform.unsetenv("NOTIFY_SOCKET")
    with Notifier() as notifier:
        h.eq(notifier.barrier_begin(), None)
        h.false(notifier.barrier(timeout_ms=0))


def test_a_barrier_is_the_only_assignment_in_its_message():
    with FakeNotifyListener() as listener:
        with Notifier(listener.address) as notifier:
            read_fd = notifier.barrier_begin()
            try:
                datagram = listener.receive(1000)
                h.eq(datagram.lines(), ["BARRIER=1"])
                datagram.close_fds()
            finally:
                _platform.close_fd(read_fd)
