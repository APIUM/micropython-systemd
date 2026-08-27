# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""A test harness small enough to run on the MicroPython unix port.

The unix port has no unittest module, so this collects test_ functions from a
module's namespace and runs them. The same code runs on CPython, which keeps the
two runtimes comparing like with like.
"""

import sys


class Failure(Exception):
    """A test assertion did not hold."""


class Skipped(Exception):
    """A test cannot run on this runtime or system."""


def fail(note):
    raise Failure(note)


def skip(reason):
    raise Skipped(reason)


def eq(actual, expected, note=""):
    if actual != expected:
        raise Failure("%sexpected %r, got %r" % (note and note + ": ", expected, actual))


def ne(actual, unexpected, note=""):
    if actual == unexpected:
        raise Failure("%sdid not expect %r" % (note and note + ": ", unexpected))


def true(value, note=""):
    if not value:
        raise Failure("%sexpected a true value, got %r" % (note and note + ": ", value))


def false(value, note=""):
    if value:
        raise Failure("%sexpected a false value, got %r" % (note and note + ": ", value))


def raises(exc_type, fn, *args, **kwargs):
    """Assert that a call raises exc_type and return the exception."""
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        return exc
    except Exception as exc:
        raise Failure(
            "expected %s, got %s: %s" % (exc_type.__name__, type(exc).__name__, exc)
        )
    raise Failure("expected %s, nothing was raised" % exc_type.__name__)


def between(value, low, high, note=""):
    if not low <= value <= high:
        raise Failure(
            "%s%r is not within %r..%r" % (note and note + ": ", value, low, high)
        )


def print_exception(exc):
    try:
        import traceback
    except ImportError:
        sys.print_exception(exc)
        return
    traceback.print_exception(type(exc), exc, exc.__traceback__)


def run_module(namespace, label):
    """Run every test_ function in namespace. Returns (passed, failed, skipped)."""
    names = sorted([name for name in namespace if name.startswith("test_")])
    passed = 0
    failed = 0
    skipped = 0
    for name in names:
        try:
            namespace[name]()
        except Skipped as exc:
            skipped += 1
            print("SKIP %s.%s: %s" % (label, name, exc))
        except Failure as exc:
            failed += 1
            print("FAIL %s.%s: %s" % (label, name, exc))
        except Exception as exc:
            failed += 1
            print("ERROR %s.%s: %r" % (label, name, exc))
            print_exception(exc)
        else:
            passed += 1
            print("pass %s.%s" % (label, name))
    return passed, failed, skipped


def run_async(coro):
    """Run one coroutine to completion, on either runtime's asyncio."""
    import asyncio

    return asyncio.run(coro)
