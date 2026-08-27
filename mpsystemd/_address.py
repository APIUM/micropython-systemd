# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Parsing of systemd AF_UNIX socket addresses.

Both runtime backends share this so that address handling cannot drift between
them. The result is the raw content of sun_path: an abstract address keeps its
leading NUL, a filesystem address does not include its NUL terminator.
"""

from .errors import AddressError

# sizeof(((struct sockaddr_un *)0)->sun_path) on Linux.
SUN_PATH_SIZE = 108

_ABSTRACT_PREFIX = b"@"
_NUL = b"\x00"


def encode_unix_address(address):
    """Return the sun_path bytes for a systemd socket address string.

    A leading "@" selects the Linux abstract namespace and is replaced by the NUL
    byte that actually selects it inside sun_path.
    """
    if isinstance(address, (bytes, bytearray)):
        raw = bytes(address)
    else:
        raw = address.encode("utf-8")
    if not raw:
        raise AddressError("socket address is empty")

    first = raw[0:1]
    if first == _ABSTRACT_PREFIX:
        raw = _NUL + raw[1:]
        first = _NUL
    if first == _NUL:
        if len(raw) > SUN_PATH_SIZE:
            raise AddressError(
                "abstract socket name is longer than %d bytes" % (SUN_PATH_SIZE - 1)
            )
        return raw
    if first != b"/":
        if raw.startswith(b"vsock:") or raw.startswith(b"vsock-"):
            raise AddressError("AF_VSOCK socket addresses are not supported")
        raise AddressError("socket address must be absolute or abstract: %r" % (raw,))
    # A filesystem address needs room for its NUL terminator inside sun_path.
    if len(raw) > SUN_PATH_SIZE - 1:
        raise AddressError(
            "socket path is longer than %d bytes" % (SUN_PATH_SIZE - 1)
        )
    return raw


def is_abstract(sun_path):
    """True when sun_path names an abstract namespace socket."""
    return sun_path[0:1] == _NUL


def describe(sun_path):
    """Return a printable form of raw sun_path bytes."""
    if is_abstract(sun_path):
        return "@" + sun_path[1:].decode("utf-8", "replace")
    return sun_path.decode("utf-8", "replace")
