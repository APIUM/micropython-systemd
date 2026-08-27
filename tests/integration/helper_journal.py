# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Submit journal entries the driver then reads back with journalctl.

Three entries: a plain one, one whose message holds newlines so that the length
prefixed field form is used, and one large enough to force the sealed memfd
transport.
"""

import sys

from mpsystemd import journal

TOKEN = sys.argv[1]
LARGE_BYTES = 256 * 1024

writer = journal.JournalWriter()
writer.send("plain entry", priority=journal.INFO, mpsd_token=TOKEN, mpsd_kind="plain")
writer.send(
    "first line\nsecond line",
    priority=journal.WARNING,
    mpsd_token=TOKEN,
    mpsd_kind="multiline",
)
writer.send(
    "x" * LARGE_BYTES,
    priority=journal.INFO,
    mpsd_token=TOKEN,
    mpsd_kind="large",
)
writer.close()
print("submitted")
