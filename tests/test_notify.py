# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Notification protocol tests. None of these need systemd to be running."""

import os
import struct

import harness as h

import mpsystemd
from mpsystemd import _platform, daemon
from mpsystemd._address import SUN_PATH_SIZE, encode_unix_address
from mpsystemd.errors import AddressError
from mpsystemd.testing import FakeNotifyListener

TMP_DIR = "/tmp"


def _clear_environment():
    for name in ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID"):
        _platform.unsetenv(name)


# --- payload building ------------------------------------------------------


def test_payload_single_field():
    h.eq(daemon.build_payload((), {"READY": 1}), b"READY=1\n")


def test_payload_upper_cases_names():
    h.eq(daemon.build_payload((), {"ready": 1}), b"READY=1\n")


def test_payload_sorts_names():
    payload = daemon.build_payload((), {"STATUS": "b", "READY": 1, "MAINPID": 7})
    h.eq(payload, b"MAINPID=7\nREADY=1\nSTATUS=b\n")


def test_payload_one_datagram_for_many_assignments():
    payload = daemon.build_payload((), {"READY": 1, "STATUS": "up"})
    h.eq(payload.count(b"\n"), 2)
    h.eq(payload[-1:], b"\n")


def test_payload_accepts_raw_state_strings():
    h.eq(daemon.build_payload(("READY=1", "STATUS=x"), None), b"READY=1\nSTATUS=x\n")


def test_payload_keeps_newlines_inside_raw_strings():
    h.eq(daemon.build_payload(("READY=1\nSTATUS=x",), None), b"READY=1\nSTATUS=x\n")


def test_payload_formats_bool_as_one_or_zero():
    h.eq(daemon.build_payload((), {"FDPOLL": False}), b"FDPOLL=0\n")
    h.eq(daemon.build_payload((), {"FDPOLL": True}), b"FDPOLL=1\n")


def test_payload_rejects_newline_in_value():
    h.raises(ValueError, daemon.build_payload, (), {"STATUS": "a\nREADY=1"})


def test_payload_rejects_name_with_punctuation():
    h.raises(ValueError, daemon.build_payload, (), {"MY-FIELD": 1})


def test_payload_rejects_name_starting_with_digit():
    h.raises(ValueError, daemon.build_payload, (), {"1READY": 1})


def test_payload_rejects_empty():
    h.raises(ValueError, daemon.build_payload, (), {})


# --- addresses -------------------------------------------------------------


def test_address_abstract_becomes_leading_nul():
    h.eq(encode_unix_address("@thing"), b"\x00thing")


def test_address_path_is_unchanged():
    h.eq(encode_unix_address("/run/x"), b"/run/x")


def test_address_rejects_relative():
    h.raises(AddressError, encode_unix_address, "relative/path")


def test_address_rejects_vsock():
    exc = h.raises(AddressError, encode_unix_address, "vsock:2:1234")
    h.true("VSOCK" in str(exc), "the error does not name the transport it refused")


def test_address_rejects_empty():
    h.raises(AddressError, encode_unix_address, "")


def test_address_rejects_overlong_path():
    encode_unix_address("/" + "a" * (SUN_PATH_SIZE - 2))
    h.raises(AddressError, encode_unix_address, "/" + "a" * (SUN_PATH_SIZE - 1))


def test_address_rejects_overlong_abstract_name():
    encode_unix_address("@" + "a" * (SUN_PATH_SIZE - 1))
    h.raises(AddressError, encode_unix_address, "@" + "a" * SUN_PATH_SIZE)


def test_the_sockaddr_length_stops_before_the_trailing_nul():
    """Both sides of a socket share this code, so pin the layout directly.

    An address length that included the terminator would make the terminator
    part of an abstract namespace name.
    """
    if not _platform.MICROPYTHON:
        h.skip("only the MicroPython backend builds sockaddr_un by hand")
    from mpsystemd import _impl_mp

    sun_path = b"\x00abstract-name"
    name, namelen = _impl_mp._sockaddr_un(sun_path)
    h.eq(bytes(name), struct.pack("H", _impl_mp.AF_UNIX) + sun_path + b"\x00")
    h.eq(namelen, struct.calcsize("H") + len(sun_path))


# --- behaviour with no notification socket ---------------------------------


def test_notify_without_socket_is_falsey():
    _clear_environment()
    h.false(mpsystemd.notify_available())
    h.false(mpsystemd.ready())
    h.false(mpsystemd.status("anything"))
    h.false(mpsystemd.watchdog_ping())
    h.false(mpsystemd.barrier(timeout_ms=0))


def test_notifier_without_socket_reports_unavailable():
    _clear_environment()
    with mpsystemd.Notifier() as notifier:
        h.false(notifier.available)
        h.false(notifier.notify(READY=1))


def test_notify_available_false_for_bad_address():
    _platform.setenv("NOTIFY_SOCKET", "not-absolute")
    try:
        h.false(mpsystemd.notify_available())
    finally:
        _clear_environment()


# --- delivery over a real socket -------------------------------------------


def test_ready_reaches_the_listener():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            h.true(notifier.ready())
        datagram = listener.receive(1000)
        h.ne(datagram, None, "no datagram arrived")
        h.eq(datagram.fields(), {"READY": "1"})


def test_many_assignments_share_one_datagram():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            notifier.notify(READY=1, STATUS="up", MAINPID=4242)
        received = listener.receive_all(1000)
        h.eq(len(received), 1, "assignments were split across datagrams")
        h.eq(
            received[0].fields(),
            {"READY": "1", "STATUS": "up", "MAINPID": "4242"},
        )


def test_named_helpers_send_the_right_assignments():
    cases = (
        ("ready", (), {"READY": "1"}),
        ("stopping", (), {"STOPPING": "1"}),
        ("status", ("halfway",), {"STATUS": "halfway"}),
        ("errno", (13,), {"ERRNO": "13"}),
        ("main_pid", (99,), {"MAINPID": "99"}),
        ("extend_timeout", (30000000,), {"EXTEND_TIMEOUT_USEC": "30000000"}),
        ("watchdog_ping", (), {"WATCHDOG": "1"}),
        ("watchdog_trigger", (), {"WATCHDOG": "trigger"}),
    )
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            for name, args, expected in cases:
                getattr(notifier, name)(*args)
                datagram = listener.receive(1000)
                h.ne(datagram, None, name)
                h.eq(datagram.fields(), expected, name)


def test_ready_can_include_status():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            notifier.ready("serving")
        h.eq(
            listener.receive(1000).fields(),
            {"READY": "1", "STATUS": "serving"},
        )


def test_main_pid_defaults_to_this_process():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            notifier.main_pid()
        h.eq(
            listener.receive(1000).fields(),
            {"MAINPID": str(_platform.getpid())},
        )


def test_reloading_includes_monotonic_usec():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            before = _platform.monotonic_us()
            notifier.reloading()
            after = _platform.monotonic_us()
        fields = listener.receive(1000).fields()
        h.eq(fields.get("RELOADING"), "1")
        h.ne(fields.get("MONOTONIC_USEC"), None, "MONOTONIC_USEC is missing")
        h.between(int(fields["MONOTONIC_USEC"]), before, after)


def test_reloading_can_include_status():
    with FakeNotifyListener() as listener:
        with mpsystemd.Notifier(listener.address) as notifier:
            notifier.reloading("re-reading configuration")
        fields = listener.receive(1000).fields()
        h.eq(fields.get("STATUS"), "re-reading configuration")
        h.eq(fields.get("RELOADING"), "1")


def test_filesystem_path_address_works():
    path = "%s/mpsystemd-test-%d.sock" % (TMP_DIR, _platform.getpid())
    try:
        os.remove(path)
    except OSError:
        pass
    listener = FakeNotifyListener(path)
    try:
        with mpsystemd.Notifier(path) as notifier:
            notifier.ready()
        h.eq(listener.receive(1000).fields(), {"READY": "1"})
    finally:
        listener.close()
        try:
            os.remove(path)
        except OSError:
            pass


def test_module_functions_read_notify_socket_from_the_environment():
    with FakeNotifyListener() as listener:
        _platform.setenv("NOTIFY_SOCKET", listener.address)
        try:
            h.true(mpsystemd.notify_available())
            h.true(mpsystemd.ready())
            h.eq(listener.receive(1000).fields(), {"READY": "1"})
            h.true(mpsystemd.status("working"))
            h.eq(listener.receive(1000).fields(), {"STATUS": "working"})
        finally:
            _clear_environment()


def test_unset_notify_environment_stops_notification():
    with FakeNotifyListener() as listener:
        _platform.setenv("NOTIFY_SOCKET", listener.address)
        try:
            mpsystemd.unset_notify_environment()
            h.false(mpsystemd.notify_available())
            h.false(mpsystemd.ready())
            h.eq(listener.receive(200), None, "a datagram arrived after unsetting")
        finally:
            _clear_environment()


def test_send_raw_reports_delivery_failure():
    listener = FakeNotifyListener()
    address = listener.address
    listener.close()
    with mpsystemd.Notifier(address) as notifier:
        h.raises(OSError, notifier.ready)
