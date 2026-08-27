# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Exception types raised by mpsystemd."""


class SystemdError(Exception):
    """Base class for every error this package raises deliberately."""


class AddressError(SystemdError):
    """A systemd socket address is malformed or uses an unsupported transport."""


class JournalError(SystemdError):
    """A journal entry could not be built or submitted."""


class UnsupportedError(SystemdError):
    """The running platform cannot provide the requested facility."""
