# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Small asyncio shims that differ between the two runtimes."""

import asyncio

_MS_PER_SEC = 1000

try:
    _sleep_ms = asyncio.sleep_ms
except AttributeError:

    def _sleep_ms(ms):
        return asyncio.sleep(ms / _MS_PER_SEC)


def sleep_ms(ms):
    """Return an awaitable that completes after ms milliseconds."""
    return _sleep_ms(ms)
