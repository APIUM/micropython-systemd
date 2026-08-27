# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Algy Tynan
"""Integration tests that need a running systemd. Driver runs under CPython.

    python3 tests/integration/run_integration.py [INTERPRETER ...]

Each interpreter named on the command line runs the service helpers, so the same
protocol work can be checked on CPython and on the MicroPython unix port. With no
arguments the driver uses the interpreter it is running under.

By default the transient units go to the calling user's service manager. Set
MPSYSTEMD_IT_SCOPE=system to use the system manager instead, which needs root:

    sudo -E MPSYSTEMD_IT_SCOPE=system python3 tests/integration/run_integration.py

The driver reports SKIP and exits 0 when there is no service manager it can use,
so it is safe to call unconditionally from CI.
"""

import binascii
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

READY_STATUS = "serving"
RELOADED_STATUS = "second status"

ACTIVE_TIMEOUT_S = 20.0
PROPERTY_TIMEOUT_S = 10.0
JOURNAL_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.1

WATCHDOG_SEC = 2
WATCHDOG_PING_SECONDS = 5.0
WATCHDOG_SURVIVE_S = 4.0
WATCHDOG_FAIL_TIMEOUT_S = 15.0

LARGE_ENTRY_BYTES = 256 * 1024

SCOPE = os.environ.get("MPSYSTEMD_IT_SCOPE", "user")
if SCOPE not in ("user", "system"):
    raise SystemExit("MPSYSTEMD_IT_SCOPE must be user or system")
SCOPE_FLAG = "--" + SCOPE

_failures = []
_passes = []


def unit_name(kind):
    suffix = binascii.hexlify(os.urandom(4)).decode("ascii")
    return "mpsystemd-it-%s-%s" % (kind, suffix)


def helper_environment():
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO
    env["MICROPYPATH"] = ".frozen:" + REPO
    return env


def run(argv, check=False, timeout=60):
    result = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "%s exited %d: %s" % (" ".join(argv), result.returncode, result.stdout)
        )
    return result


def show(unit, prop):
    result = run(["systemctl", SCOPE_FLAG, "show", "-p", prop, "--value", unit])
    return result.stdout.strip()


def wait_for(unit, prop, wanted, timeout_s):
    deadline = time.monotonic() + timeout_s
    seen = None
    while time.monotonic() < deadline:
        seen = show(unit, prop)
        if seen == wanted:
            return True
        time.sleep(POLL_INTERVAL_S)
    print("      %s stayed at %r, wanted %r" % (prop, seen, wanted))
    return False


def cleanup(unit):
    run(["systemctl", SCOPE_FLAG, "stop", unit])
    run(["systemctl", SCOPE_FLAG, "reset-failed", unit])


def read_marker(path):
    parsed = {}
    if not os.path.exists(path):
        return parsed
    with open(path) as marker:
        for line in marker:
            line = line.strip()
            if "=" in line:
                name, value = line.split("=", 1)
                parsed[name] = value
    return parsed


def check(condition, note):
    if condition:
        _passes.append(note)
        print("    pass %s" % note)
    else:
        _failures.append(note)
        print("    FAIL %s" % note)


def systemd_usable():
    """Report why the integration tests cannot run here, or None when they can."""
    for tool in ("systemd-run", "systemctl", "journalctl"):
        if shutil.which(tool) is None:
            return "%s is not installed" % tool
    if not os.path.isdir("/run/systemd/system"):
        return "the system did not boot with systemd"
    # Starting a trivial unit is a better check than reading the manager state,
    # which reports degraded on plenty of systems where transient units work.
    probe = run(
        [
            "systemd-run",
            SCOPE_FLAG,
            "--quiet",
            "--wait",
            "--collect",
            "--unit=" + unit_name("probe"),
            "/bin/true",
        ]
    )
    if probe.returncode != 0:
        return "systemd-run %s failed: %s" % (SCOPE_FLAG, probe.stdout.strip())
    return None


# --- test: notification protocol under a real Type=notify unit --------------


def test_notify(interpreter, label):
    unit = unit_name("notify")
    marker = "/tmp/%s.marker" % unit
    env = helper_environment()
    argv = [
        "systemd-run",
        SCOPE_FLAG,
        "--quiet",
        "--unit=" + unit,
        "--service-type=notify",
        "--property=NotifyAccess=main",
        "--property=FileDescriptorStoreMax=4",
        "--setenv=PYTHONPATH=" + env["PYTHONPATH"],
        "--setenv=MICROPYPATH=" + env["MICROPYPATH"],
        "--",
    ] + interpreter + [os.path.join(HERE, "helper_service.py"), marker]
    print("  %s: Type=notify service" % label)
    try:
        started = run(argv)
        check(started.returncode == 0, "%s systemd-run started the unit" % label)
        check(
            wait_for(unit, "ActiveState", "active", ACTIVE_TIMEOUT_S),
            "%s the unit became active, so READY=1 arrived" % label,
        )
        check(
            wait_for(unit, "StatusText", READY_STATUS, PROPERTY_TIMEOUT_S),
            "%s STATUS reached systemd" % label,
        )
        check(
            wait_for(unit, "NFileDescriptorStore", "1", PROPERTY_TIMEOUT_S),
            "%s FDSTORE=1 put a descriptor in the store" % label,
        )
        run(["systemctl", SCOPE_FLAG, "kill", "--signal=SIGHUP", unit])
        check(
            wait_for(unit, "StatusText", RELOADED_STATUS, PROPERTY_TIMEOUT_S),
            "%s SIGHUP was delivered and STATUS changed" % label,
        )
        run(["systemctl", SCOPE_FLAG, "stop", unit], timeout=60)
        check(show(unit, "Result") in ("success", ""), "%s the unit stopped cleanly" % label)
        observed = read_marker(marker)
        check(observed.get("notify_available") == "True", "%s NOTIFY_SOCKET was set" % label)
        check(observed.get("booted") == "True", "%s booted() saw systemd" % label)
        check(observed.get("barrier") == "True", "%s BARRIER=1 was acknowledged" % label)
        check(observed.get("fdstore") == "True", "%s FDSTORE=1 was accepted" % label)
        check(observed.get("stop_signal") == "15", "%s the stop arrived as SIGTERM" % label)
        check(observed.get("listen_fds") == "0", "%s no activation descriptors were passed" % label)
    finally:
        cleanup(unit)
        if os.path.exists(marker):
            os.remove(marker)


# --- test: a real WatchdogSec ----------------------------------------------


def test_watchdog(interpreter, label):
    unit = unit_name("watchdog")
    marker = "/tmp/%s.marker" % unit
    env = helper_environment()
    argv = [
        "systemd-run",
        SCOPE_FLAG,
        "--quiet",
        "--unit=" + unit,
        "--service-type=notify",
        "--property=NotifyAccess=main",
        "--property=WatchdogSec=%ds" % WATCHDOG_SEC,
        "--setenv=PYTHONPATH=" + env["PYTHONPATH"],
        "--setenv=MICROPYPATH=" + env["MICROPYPATH"],
        "--",
    ] + interpreter + [
        os.path.join(HERE, "helper_watchdog.py"),
        marker,
        str(WATCHDOG_PING_SECONDS),
    ]
    print("  %s: WatchdogSec=%ds" % (label, WATCHDOG_SEC))
    try:
        run(argv)
        check(
            wait_for(unit, "ActiveState", "active", ACTIVE_TIMEOUT_S),
            "%s the unit became active" % label,
        )
        observed = read_marker(marker)
        check(
            observed.get("watchdog_usec") == str(WATCHDOG_SEC * 1000000),
            "%s watchdog_enabled() read WATCHDOG_USEC as %d"
            % (label, WATCHDOG_SEC * 1000000),
        )
        check(
            observed.get("period_ms") == str(WATCHDOG_SEC * 1000 // 2),
            "%s the ping period is half the interval" % label,
        )
        # The unit has to outlive several intervals, which only happens if the
        # pings are reaching systemd.
        time.sleep(WATCHDOG_SURVIVE_S)
        check(
            show(unit, "ActiveState") == "active",
            "%s the unit survived %.0f s of a %d s watchdog"
            % (label, WATCHDOG_SURVIVE_S, WATCHDOG_SEC),
        )
        # After the helper stops pinging, systemd has to notice.
        check(
            wait_for(unit, "ActiveState", "failed", WATCHDOG_FAIL_TIMEOUT_S),
            "%s the unit failed once the pings stopped" % label,
        )
        check(
            show(unit, "Result") == "watchdog",
            "%s the failure was recorded as a watchdog timeout" % label,
        )
    finally:
        cleanup(unit)
        if os.path.exists(marker):
            os.remove(marker)


# --- test: the reload handshake and its MONOTONIC_USEC ---------------------


def test_reload(interpreter, label):
    unit = unit_name("reload")
    marker = "/tmp/%s.marker" % unit
    env = helper_environment()
    argv = [
        "systemd-run",
        SCOPE_FLAG,
        "--quiet",
        "--unit=" + unit,
        "--service-type=notify-reload",
        "--property=NotifyAccess=main",
        "--setenv=PYTHONPATH=" + env["PYTHONPATH"],
        "--setenv=MICROPYPATH=" + env["MICROPYPATH"],
        "--",
    ] + interpreter + [os.path.join(HERE, "helper_reload.py"), marker]
    print("  %s: Type=notify-reload" % label)
    try:
        started = run(argv)
        if started.returncode != 0:
            print("      systemd-run refused notify-reload: %s" % started.stdout.strip())
            check(False, "%s the notify-reload unit started" % label)
            return
        check(
            wait_for(unit, "ActiveState", "active", ACTIVE_TIMEOUT_S),
            "%s the unit became active" % label,
        )
        reloaded = run(["systemctl", SCOPE_FLAG, "reload", unit], timeout=60)
        check(
            reloaded.returncode == 0,
            "%s systemctl reload completed, so RELOADING=1 with MONOTONIC_USEC was "
            "accepted" % label,
        )
        check(
            wait_for(unit, "StatusText", "serving after 1 reloads", PROPERTY_TIMEOUT_S),
            "%s the service reported its post reload status" % label,
        )
        run(["systemctl", SCOPE_FLAG, "stop", unit], timeout=60)
        observed = read_marker(marker)
        check(observed.get("reloads") == "1", "%s the service handled one reload" % label)
    finally:
        cleanup(unit)
        if os.path.exists(marker):
            os.remove(marker)


# --- test: entries land in the journal -------------------------------------


def journal_entries(token):
    result = run(
        [
            "journalctl",
            SCOPE_FLAG,
            "--since",
            "-5 min",
            # Without --all journalctl abbreviates a very long field value.
            "--all",
            "-o",
            "json",
            "MPSD_TOKEN=" + token,
        ]
    )
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            entries.append(json.loads(line))
    return entries


def test_journal(interpreter, label):
    token = binascii.hexlify(os.urandom(8)).decode("ascii")
    print("  %s: journal submission" % label)
    result = subprocess.run(
        interpreter + [os.path.join(HERE, "helper_journal.py"), token],
        env=helper_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        text=True,
    )
    check(result.returncode == 0, "%s the helper submitted entries: %s" % (label, result.stdout.strip()))
    if result.returncode != 0:
        return
    deadline = time.monotonic() + JOURNAL_TIMEOUT_S
    entries = []
    while time.monotonic() < deadline:
        entries = journal_entries(token)
        if len(entries) >= 3:
            break
        time.sleep(POLL_INTERVAL_S)
    by_kind = {}
    for entry in entries:
        by_kind[entry.get("MPSD_KIND")] = entry
    check(len(entries) >= 3, "%s all three entries reached the journal" % label)
    plain = by_kind.get("plain")
    check(
        plain is not None and plain.get("MESSAGE") == "plain entry",
        "%s the plain entry round tripped" % label,
    )
    check(
        plain is not None and plain.get("PRIORITY") == str(6),
        "%s PRIORITY round tripped" % label,
    )
    multiline = by_kind.get("multiline")
    check(
        multiline is not None
        and multiline.get("MESSAGE") == "first line\nsecond line",
        "%s the length prefixed field form round tripped" % label,
    )
    large = by_kind.get("large")
    check(
        large is not None and len(large.get("MESSAGE") or "") == LARGE_ENTRY_BYTES,
        "%s the sealed memfd transport round tripped %d bytes"
        % (label, LARGE_ENTRY_BYTES),
    )


# --- driver ----------------------------------------------------------------


def main(argv):
    reason = systemd_usable()
    if reason is not None:
        print("SKIP integration tests: %s" % reason)
        return 0
    interpreters = argv[1:] or [sys.executable]
    print("using the %s service manager" % SCOPE)
    for command in interpreters:
        interpreter = command.split()
        label = os.path.basename(interpreter[0])
        print("\n== %s ==" % label)
        for test in (test_notify, test_watchdog, test_reload, test_journal):
            try:
                test(interpreter, label)
            except Exception as exc:
                _failures.append("%s %s raised %r" % (label, test.__name__, exc))
                print("    ERROR %s: %r" % (test.__name__, exc))
    print("\n%d checks passed, %d failed" % (len(_passes), len(_failures)))
    for note in _failures:
        print("  failed: %s" % note)
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
