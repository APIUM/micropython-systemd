# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Journal field encoding and submission tests.

Encoding and submission to a fake socket need no systemd. Reading an entry back
out of the journal is in tests/integration.
"""

import struct

import harness as h

from mpsystemd import _platform, journal
from mpsystemd.errors import JournalError
from mpsystemd.testing import FakeNotifyListener

# The socket send buffer default on Linux is 212992 bytes, so this is past it.
OVERSIZE_BYTES = 512 * 1024
PREFIX_BYTES = 16


# --- field encoding --------------------------------------------------------


def test_plain_value_uses_the_text_form():
    h.eq(journal.encode_field("MESSAGE", "hello"), b"MESSAGE=hello\n")


def test_name_is_upper_cased():
    h.eq(journal.encode_field("message", "hello"), b"MESSAGE=hello\n")


def test_integer_value_is_rendered_as_text():
    h.eq(journal.encode_field("PRIORITY", 6), b"PRIORITY=6\n")


def test_bytes_value_is_passed_through():
    h.eq(journal.encode_field("BLOB", b"\x01\x02"), b"BLOB=\x01\x02\n")


def test_boolean_value_becomes_one_or_zero():
    h.eq(journal.encode_field("FLAG", True), b"FLAG=1\n")
    h.eq(journal.encode_field("FLAG", False), b"FLAG=0\n")


def test_a_value_with_a_newline_uses_the_length_prefixed_form():
    encoded = journal.encode_field("MESSAGE", "two\nlines")
    h.eq(encoded[:8], b"MESSAGE\n")
    h.eq(struct.unpack("<Q", encoded[8:16])[0], len(b"two\nlines"))
    h.eq(encoded[16:], b"two\nlines\n")


def test_the_length_prefix_is_eight_bytes_little_endian():
    encoded = journal.encode_field("M", "a\nb")
    h.eq(encoded, b"M\n" + b"\x03\x00\x00\x00\x00\x00\x00\x00" + b"a\nb" + b"\n")


def test_a_leading_underscore_is_rejected():
    h.raises(JournalError, journal.encode_field, "_PID", 1)


def test_a_leading_digit_is_rejected():
    h.raises(JournalError, journal.encode_field, "1FIELD", 1)


def test_punctuation_in_a_name_is_rejected():
    h.raises(JournalError, journal.encode_field, "MY-FIELD", 1)
    h.raises(JournalError, journal.encode_field, "MY FIELD", 1)


def test_an_empty_name_is_rejected():
    h.raises(JournalError, journal.encode_field, "", 1)


def test_an_overlong_name_is_rejected():
    journal.encode_field("A" * journal.FIELD_NAME_MAX, 1)
    h.raises(JournalError, journal.encode_field, "A" * (journal.FIELD_NAME_MAX + 1), 1)


def test_fields_are_emitted_in_sorted_name_order():
    h.eq(
        journal.encode_fields({"PRIORITY": 6, "MESSAGE": "x"}),
        b"MESSAGE=x\nPRIORITY=6\n",
    )


def test_a_sequence_of_pairs_keeps_its_own_order():
    h.eq(
        journal.encode_fields((("PRIORITY", 6), ("MESSAGE", "x"))),
        b"PRIORITY=6\nMESSAGE=x\n",
    )


def test_an_entry_with_no_fields_is_rejected():
    h.raises(JournalError, journal.encode_fields, {})


# --- priority mapping ------------------------------------------------------


def test_priority_mapping_covers_every_logging_level():
    cases = (
        (50, journal.CRIT),
        (60, journal.CRIT),
        (40, journal.ERR),
        (30, journal.WARNING),
        (20, journal.INFO),
        (10, journal.DEBUG),
        (0, journal.DEBUG),
    )
    for levelno, expected in cases:
        h.eq(journal.priority_for_level(levelno), expected, "level %d" % levelno)


# --- submission ------------------------------------------------------------


def test_an_entry_reaches_the_socket():
    with FakeNotifyListener() as listener:
        with journal.JournalWriter(listener.address) as writer:
            h.true(writer.send("hello", priority=journal.WARNING))
        datagram = listener.receive(1000)
        h.ne(datagram, None, "no entry arrived")
        h.eq(datagram.data, b"MESSAGE=hello\nPRIORITY=4\n")


def test_extra_fields_are_included():
    with FakeNotifyListener() as listener:
        with journal.JournalWriter(listener.address) as writer:
            writer.send("hello", priority=journal.INFO, unit_id="worker-1")
        h.eq(
            listener.receive(1000).fields(),
            {"MESSAGE": "hello", "PRIORITY": "6", "UNIT_ID": "worker-1"},
        )


def test_priority_can_be_left_out():
    with FakeNotifyListener() as listener:
        with journal.JournalWriter(listener.address) as writer:
            writer.send("bare", priority=None)
        h.eq(listener.receive(1000).data, b"MESSAGE=bare\n")


def test_an_oversized_entry_goes_across_as_a_sealed_memfd():
    body = b"MESSAGE=" + b"x" * OVERSIZE_BYTES + b"\n"
    with FakeNotifyListener() as listener:
        with journal.JournalWriter(listener.address) as writer:
            h.true(writer.send_raw(body))
        datagram = listener.receive(2000)
        h.ne(datagram, None, "no entry arrived")
        h.eq(datagram.data, b"", "the payload should be empty for a memfd entry")
        h.eq(len(datagram.fds), 1, "no descriptor came with the entry")
        fd = datagram.fds[0]
        try:
            h.eq(
                _platform.lseek(fd, 0, _platform.SEEK_END),
                len(body),
                "the memfd does not hold the whole entry",
            )
            _platform.lseek(fd, 0, _platform.SEEK_SET)
            h.eq(_platform.read_fd(fd, PREFIX_BYTES), body[:PREFIX_BYTES])
            h.false(_writable(fd), "the memfd was not sealed read only")
        finally:
            datagram.close_fds()


def _writable(fd):
    try:
        _platform.write_fd(fd, b"x")
    except OSError:
        return False
    return True


def test_a_writer_rejects_a_relative_address():
    from mpsystemd.errors import AddressError

    h.raises(AddressError, journal.JournalWriter, "relative/socket")


def test_availability_follows_the_socket_path():
    h.false(journal.available("/nonexistent/journal/socket"))
    h.true(journal.available("/tmp"))


# --- logging handler -------------------------------------------------------


def _detach(logger, handler):
    """Take a handler off a logger. micropython-lib has no removeHandler."""
    remove = getattr(logger, "removeHandler", None)
    if remove is not None:
        remove(handler)
        return
    logger.handlers = [known for known in logger.handlers if known is not handler]


def test_the_logging_handler_submits_structured_fields():
    try:
        import logging
    except ImportError:
        h.skip("the logging module is not installed on this runtime")
    with FakeNotifyListener() as listener:
        writer = journal.JournalWriter(listener.address)
        handler = journal.journal_handler(
            writer=writer, identifier="mpsystemd-test", fields={"COMPONENT": "tests"}
        )
        logger = logging.getLogger("mpsystemd.test.handler")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            logger.warning("a warning")
            fields = listener.receive(1000).fields()
        finally:
            _detach(logger, handler)
            writer.close()
    h.eq(fields.get("MESSAGE"), "a warning")
    h.eq(fields.get("PRIORITY"), str(journal.WARNING))
    h.eq(fields.get("LOGGER"), "mpsystemd.test.handler")
    h.eq(fields.get("SYSLOG_IDENTIFIER"), "mpsystemd-test")
    h.eq(fields.get("COMPONENT"), "tests")
