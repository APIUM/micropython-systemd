# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Small checks about the surrounding system."""

import os

# systemd creates this directory only when it is running as the init system.
SYSTEMD_RUNTIME_DIR = "/run/systemd/system"


def booted(path=SYSTEMD_RUNTIME_DIR):
    """True when the system booted with systemd as its init system.

    This is not the same as running under systemd supervision. A container or a
    chroot can be started by something else while systemd runs on the host.
    """
    try:
        os.stat(path)
    except OSError:
        return False
    return True
