# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Run the unit test suite under either runtime.

    python3 tests/run_tests.py
    MICROPYPATH=".frozen:$PWD" micropython tests/run_tests.py

None of these tests need systemd to be running. The tests in tests/integration
do.
"""

import sys

MODULES = (
    "test_notify",
    "test_watchdog",
    "test_activation",
    "test_signals",
    "test_journal",
    "test_fdstore",
)


def _add_paths():
    """Make the package and the tests importable however this was started."""
    here = None
    argv0 = sys.argv[0] if sys.argv else ""
    if "/" in argv0:
        here = argv0.rsplit("/", 1)[0]
    else:
        here = "tests"
    root = here.rsplit("/", 1)[0] if "/" in here else "."
    for path in (here, root):
        if path not in sys.path:
            sys.path.insert(0, path)


def main():
    _add_paths()
    import harness

    from mpsystemd import MICROPYTHON, __version__

    print(
        "mpsystemd %s on %s"
        % (__version__, "MicroPython" if MICROPYTHON else "CPython")
    )
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    for name in MODULES:
        module = __import__(name)
        passed, failed, skipped = harness.run_module(module.__dict__, name)
        total_passed += passed
        total_failed += failed
        total_skipped += skipped
    print(
        "\n%d passed, %d failed, %d skipped"
        % (total_passed, total_failed, total_skipped)
    )
    return 1 if total_failed else 0


sys.exit(main())
