# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Structured logging to the journal's native datagram socket.

journald listens on an AF_UNIX datagram socket and accepts entries as a series of
fields. A field whose value holds no newline goes out as NAME=value followed by a
newline. Any other value goes out as the name, a newline, the value length as a
64 bit little endian integer, the value, and a newline. No part of libsystemd is
involved.
"""

import os
import struct
import sys

from . import _platform
from ._address import encode_unix_address
from .errors import JournalError, UnsupportedError

JOURNAL_SOCKET = "/run/systemd/journal/socket"

EMERG = 0
ALERT = 1
CRIT = 2
ERR = 3
WARNING = 4
NOTICE = 5
INFO = 6
DEBUG = 7

# journald rejects a longer field name.
FIELD_NAME_MAX = 64

# The datagram form is abandoned for a sealed memfd on these errors.
_EMSGSIZE = 90
_ENOBUFS = 105

_NAME_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_DIGITS = "0123456789"

_LENGTH_PREFIX = "<Q"


def check_field_name(name):
    """Validate a journal field name, upper casing it first.

    A leading underscore is reserved for the fields journald adds itself, and a
    leading digit is not allowed.
    """
    upper = name.upper()
    if not upper:
        raise JournalError("journal field name is empty")
    if len(upper) > FIELD_NAME_MAX:
        raise JournalError(
            "journal field name is longer than %d characters: %r" % (FIELD_NAME_MAX, name)
        )
    if upper[0] == "_":
        raise JournalError(
            "journal field names starting with an underscore are reserved: %r" % (name,)
        )
    if upper[0] in _DIGITS:
        raise JournalError("journal field name starts with a digit: %r" % (name,))
    for char in upper:
        if char not in _NAME_CHARS:
            raise JournalError(
                "journal field name has an invalid character: %r" % (name,)
            )
    return upper


def _as_bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if value is True:
        return b"1"
    if value is False:
        return b"0"
    return str(value).encode("utf-8")


def encode_field(name, value):
    """Serialise one journal field, choosing the text or length prefixed form."""
    upper = check_field_name(name)
    raw = _as_bytes(value)
    if b"\n" in raw:
        return (
            upper.encode("ascii")
            + b"\n"
            + struct.pack(_LENGTH_PREFIX, len(raw))
            + raw
            + b"\n"
        )
    return upper.encode("ascii") + b"=" + raw + b"\n"


def encode_fields(fields):
    """Serialise a mapping or a sequence of pairs into a journal entry body.

    A mapping is emitted in sorted name order so that the result does not depend
    on dictionary ordering, which differs between the runtimes.
    """
    if hasattr(fields, "keys"):
        items = [(name, fields[name]) for name in sorted(fields.keys())]
    else:
        items = list(fields)
    if not items:
        raise JournalError("journal entry has no fields")
    return b"".join([encode_field(name, value) for name, value in items])


# Level numbers used by the logging module on both runtimes.
LOG_CRITICAL = 50
LOG_ERROR = 40
LOG_WARNING = 30
LOG_INFO = 20


def priority_for_level(levelno):
    """Map a logging level number onto a syslog priority."""
    if levelno >= LOG_CRITICAL:
        return CRIT
    if levelno >= LOG_ERROR:
        return ERR
    if levelno >= LOG_WARNING:
        return WARNING
    if levelno >= LOG_INFO:
        return INFO
    return DEBUG


def available(path=JOURNAL_SOCKET):
    """True when the journal's datagram socket is present."""
    try:
        os.stat(path)
    except OSError:
        return False
    return True


class JournalWriter:
    """A socket to the journal held open across many entries."""

    def __init__(self, address=JOURNAL_SOCKET):
        self.address = address
        self._sun_path = encode_unix_address(address)
        self._sock = None

    @property
    def available(self):
        return available(self.address)

    def _socket(self):
        if self._sock is None:
            self._sock = _platform.UnixDatagram(peer=self._sun_path)
        return self._sock

    def send_fields(self, fields):
        """Submit an already assembled mapping or sequence of pairs."""
        return self.send_raw(encode_fields(fields))

    def send_raw(self, body):
        """Submit a serialised entry body.

        An entry too large for the socket buffer goes across as a sealed memfd
        instead, which is the transport journald expects for large entries.
        """
        sock = self._socket()
        try:
            sock.send(body)
            return True
        except OSError as exc:
            if exc.args[0] not in (_EMSGSIZE, _ENOBUFS):
                raise
        fd = _platform.memfd_sealed("mpsystemd-journal", body)
        try:
            sock.send_with_fds(b"", (fd,))
        finally:
            _platform.close_fd(fd)
        return True

    def send(self, message=None, priority=INFO, **fields):
        """Submit an entry, with MESSAGE and PRIORITY filled in for convenience."""
        entry = {}
        for name in fields:
            entry[name] = fields[name]
        if message is not None:
            entry["MESSAGE"] = message
        if priority is not None:
            entry["PRIORITY"] = int(priority)
        return self.send_fields(entry)

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_default_writer = None


def _writer():
    global _default_writer
    if _default_writer is None:
        _default_writer = JournalWriter()
    return _default_writer


def send(message=None, priority=INFO, **fields):
    """Submit one entry through a shared writer."""
    return _writer().send(message, priority, **fields)


def send_fields(fields):
    """Submit an assembled entry through a shared writer."""
    return _writer().send_fields(fields)


def close():
    """Close the shared writer's socket."""
    global _default_writer
    if _default_writer is not None:
        _default_writer.close()
        _default_writer = None


_handler_class = None


def _exception_text(exc_info):
    try:
        import traceback
    except ImportError:
        return None
    return "".join(traceback.format_exception(*exc_info)).rstrip("\n")


def _build_handler_class():
    try:
        import logging
    except ImportError:
        raise UnsupportedError(
            "the logging module is not built into this runtime, install it with "
            "`mip install logging`"
        )

    class JournalHandler(logging.Handler):
        """A logging handler that submits records as structured journal fields."""

        def __init__(self, level=0, writer=None, identifier=None, fields=None):
            logging.Handler.__init__(self, level)
            self.writer = writer if writer is not None else _writer()
            self.identifier = identifier
            self.static_fields = dict(fields) if fields else {}

        def _message(self, record):
            formatter = getattr(self, "formatter", None)
            if formatter is not None:
                return formatter.format(record)
            get_message = getattr(record, "getMessage", None)
            if get_message is not None:
                return get_message()
            return record.message

        def emit(self, record):
            try:
                message = self._message(record)
                exc_info = getattr(record, "exc_info", None)
                if exc_info:
                    text = _exception_text(exc_info)
                    if text:
                        message = message + "\n" + text
                entry = {}
                for name in self.static_fields:
                    entry[name] = self.static_fields[name]
                entry["MESSAGE"] = message
                entry["PRIORITY"] = priority_for_level(record.levelno)
                entry["LOGGER"] = record.name
                if self.identifier is not None:
                    entry["SYSLOG_IDENTIFIER"] = self.identifier
                for attribute, field in (
                    ("pathname", "CODE_FILE"),
                    ("lineno", "CODE_LINE"),
                    ("funcName", "CODE_FUNC"),
                ):
                    value = getattr(record, attribute, None)
                    if value is not None:
                        entry[field] = value
                self.writer.send_fields(entry)
            except Exception as exc:
                # A failed log call must not take the service down.
                handle_error = getattr(self, "handleError", None)
                if handle_error is not None:
                    handle_error(record)
                else:
                    sys.stderr.write("journal handler failed: %r\n" % (exc,))

    return JournalHandler


def journal_handler(level=0, writer=None, identifier=None, fields=None):
    """Return a logging handler that submits records to the journal.

    The logging module is not built into the MicroPython unix port. Install it
    with `mip install logging` before calling this.
    """
    global _handler_class
    if _handler_class is None:
        _handler_class = _build_handler_class()
    return _handler_class(
        level=level, writer=writer, identifier=identifier, fields=fields
    )
