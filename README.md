<!-- SPDX-License-Identifier: MIT -->
<!-- Copyright (c) 2026 Algy Tynan -->

# micropython-systemd

systemd service integration for the MicroPython unix port and for CPython, from one
source tree, with no libsystemd and no C extension.

A MicroPython service on an embedded Linux board is usually started by systemd, and
usually cannot use any of the existing Python bindings: `python-systemd` is a C
extension, and the pure Python alternatives lean on `socket.AF_UNIX`, the `signal`
module or `subprocess`, none of which work on the unix port. This package speaks the
systemd protocols directly. On MicroPython it reaches libc through `ffi`, and on
CPython it uses the standard library, behind one API.

Everything here has been checked against systemd 255, including a real `Type=notify`
unit, a real `WatchdogSec=`, a real `systemctl reload` handshake and entries read
back out of the journal.

## What it does

- **Notification protocol.** `ready()`, `stopping()`, `reloading()`, `status()`,
  `errno()`, `main_pid()`, `extend_timeout()`, and a general `notify()` that puts any
  number of assignments in one datagram.
- **Watchdog.** `watchdog_enabled()` honouring both `WATCHDOG_USEC` and
  `WATCHDOG_PID`, `watchdog_ping()`, `watchdog_trigger()`, and an asyncio task that
  services the watchdog at half the interval.
- **Socket activation.** `listen_fds()`, `listen_fd_names()` and
  `listen_fds_with_names()`, honouring `LISTEN_FDS`, `LISTEN_PID` and
  `LISTEN_FDNAMES`, marking the inherited descriptors close on exec the way
  `sd_listen_fds` does.
- **Signals.** One async API over `signalfd(2)` on MicroPython and over the event
  loop's own signal handling on CPython.
- **Journal.** Structured fields written straight to the journal's datagram socket,
  including the length prefixed form for values with newlines, the sealed memfd
  transport for entries too large for a datagram, and a `logging` handler.
- **Descriptor store.** `FDSTORE=1` and `FDSTOREREMOVE=1` over `SCM_RIGHTS`, with the
  `msghdr` and `cmsghdr` built by hand on MicroPython.
- **Barrier.** `BARRIER=1`, so a service can wait until systemd has processed
  everything it sent earlier.
- **System checks.** `booted()` and `notify_available()`.

## What it deliberately does not do

- **No `AF_VSOCK` notification addresses.** systemd can set `NOTIFY_SOCKET` to
  `vsock:CID:PORT` when a service runs in a VM that notifies its host. This package
  raises `AddressError` for that rather than pretending to support it.
- **No D-Bus.** Anything that needs to talk to `org.freedesktop.systemd1`, such as
  reading unit state or starting other units, is out of scope. Call `systemctl` for
  that, or use a D-Bus library.
- **No journal reading.** Only submission. Reading needs the journal file format,
  which is a much larger job than the submission protocol. Use `journalctl`.
- **No `sd_id128`, no `sd_bus`, no `sd_login`, no `sd_path`.** This is not a libsystemd
  port.
- **No signal handler callbacks on MicroPython.** See the gotchas below.

## Installation

The import name is `mpsystemd`. See "Why mpsystemd" below.

### MicroPython unix port

```sh
micropython -m mip install github:APIUM/micropython-systemd
```

`mip` puts the package in the first writable entry of `sys.path`, usually
`~/.micropython/lib`. To install somewhere else:

```sh
micropython -m mip install --target /usr/lib/micropython github:APIUM/micropython-systemd
```

The `logging` handler in `mpsystemd.journal` is the only part that needs anything
extra, because the unix port does not build in `logging`:

```sh
micropython -m mip install logging
```

The port must have `ffi` and `uctypes` compiled in. Both are on by default in the
`standard` variant of `ports/unix`, and the build needs libffi:

```sh
micropython -c 'import ffi, uctypes; print("ready")'
```

### CPython

```sh
pip install micropython-systemd
```

CPython 3.11 or newer on Linux. Nothing outside the standard library.

## Usage

### A service that starts, runs and stops

```python
import asyncio
import mpsystemd
from mpsystemd import signals, watchdog

async def main():
    mpsystemd.ready("serving")
    watchdog_task = watchdog.start_watchdog()
    try:
        await signals.wait_for_shutdown()
    finally:
        mpsystemd.stopping()
        if watchdog_task is not None:
            watchdog_task.cancel()

asyncio.run(main())
```

```ini
[Service]
Type=notify
WatchdogSec=30s
ExecStart=/usr/bin/micropython /usr/lib/myservice/main.py
```

Outside systemd every notification call returns a falsey value instead of raising, so
the same script runs from a shell.

### Notification

```python
import mpsystemd

mpsystemd.ready()
mpsystemd.status("processing batch 4")
mpsystemd.extend_timeout(30_000_000)

# Several assignments in one datagram. Keyword names are upper cased.
mpsystemd.notify(ready=1, status="serving", mainpid=1234)

# A socket held open, for a hot path.
with mpsystemd.Notifier() as notifier:
    for _ in range(1000):
        notifier.watchdog_ping()
```

`reloading()` sends `MONOTONIC_USEC` alongside `RELOADING=1`, which the protocol
requires so that systemd can tell a reload that began before its request from one that
began after. There is nothing for the caller to do.

```python
mpsystemd.reloading("re-reading configuration")
reload_the_configuration()
mpsystemd.ready("serving")
```

### Watchdog

```python
from mpsystemd import watchdog

interval_us = mpsystemd.watchdog_enabled()   # 0 when there is no watchdog
task = watchdog.start_watchdog()             # None when there is no watchdog
```

`watchdog_enabled()` returns 0 when `WATCHDOG_PID` names a different process, so a
child that inherited the environment does not service its parent's watchdog.
`start_watchdog()` pings at half the interval, which is the documented convention.

### Socket activation

```python
import mpsystemd

count = mpsystemd.listen_fds()                        # descriptors start at 3
by_name = mpsystemd.listen_fds_with_names()           # {"http": [3], "control": [4]}
```

A name can appear on more than one descriptor, because several sockets in one `.socket`
unit can share a `FileDescriptorName=`, so each value is a list. Pass
`unset_environment=True` to remove the `LISTEN_*` variables so that child processes do
not see them.

### Signals

```python
from mpsystemd import signals

# The simple case.
signo = await signals.wait_for_shutdown()

# An arbitrary set, held open across a service's whole life.
async with signals.SignalWatcher((signals.SIGHUP, signals.SIGTERM)) as watcher:
    while True:
        signo = await watcher.wait()
        if signo == signals.SIGHUP:
            reload_the_configuration()
            continue
        break
```

`poll_ms` sets how often the signalfd is read on MicroPython. It defaults to 200 ms,
because a service runs for weeks on one slow core. CPython is woken by the event loop
and ignores it.

### Journal

```python
from mpsystemd import journal

journal.send("disk filled up", priority=journal.ERR, device="/dev/mmcblk0p2")
```

Keyword names are upper cased into journal fields, so `device=` becomes `DEVICE=`. A
value with a newline in it goes out in the length prefixed form automatically, and an
entry too large for the socket buffer goes across as a sealed memfd.

As a `logging` handler:

```python
import logging
from mpsystemd import journal

logging.getLogger().addHandler(
    journal.journal_handler(identifier="myservice")
)
```

### Descriptor store and barrier

```python
import mpsystemd

mpsystemd.fdstore(listening_fd, name="listener")
mpsystemd.fdstore_remove("listener")

mpsystemd.notify(status="about to exec")
mpsystemd.barrier(timeout_ms=5000)   # everything above has been processed
```

`FileDescriptorStoreMax=` has to be set on the unit or systemd ignores `FDSTORE=1`.
systemd also watches a stored descriptor and drops it on `POLLHUP` or `POLLERR`, so
storing the read end of a pipe whose write end you then close will not keep it. Pass
`poll=False` to switch that watching off.

### Testing your own service

`mpsystemd.testing` gives you a listener that stands in for systemd, which matters on
MicroPython because the unix port cannot bind an `AF_UNIX` socket by itself.

```python
from mpsystemd.testing import FakeNotifyListener
import mpsystemd

with FakeNotifyListener() as listener:
    with mpsystemd.Notifier(listener.address) as notifier:
        notifier.ready("up")
    assert listener.receive(1000).fields() == {"READY": "1", "STATUS": "up"}
```

## Gotchas

### `socket.AF_UNIX` exists on the unix port but does not work

The MicroPython unix port exports `socket.AF_UNIX` as a constant equal to 1, so a
feature check passes. It never builds a `sockaddr_un`. Addresses go through
`getaddrinfo`, so `connect()` on a filesystem path fails with `EINVAL` even for a short
path. The constant makes the feature look present until it fails at runtime. That is
the main reason this package exists, and why every `AF_UNIX` operation on MicroPython
goes through libc by way of `ffi`.

### There is no `signal` module, and an ffi callback must not be one

The unix port has no `signal` module. The only safe way to see a signal is
`signalfd(2)`: block the signals with `sigprocmask`, then read the resulting
descriptor. Installing an `ffi` callback as a signal handler would re-enter the
interpreter from signal context, which is not safe, so this package does not offer a
handler callback API on MicroPython at all. `SignalWatcher` blocks the signals it names
when it opens and restores the previous mask when it closes. A named signal that
arrived while the watcher was open and was never read is delivered with its default
action at that moment, which for `SIGTERM` ends the process. Keep the watcher open for
as long as the service runs.

### `MICROPYPATH` replaces the search path, it does not add to it

Setting `MICROPYPATH=/usr/lib/myservice` hides the frozen modules. `import mpsystemd`
still works, since it needs nothing frozen, and the first symptom shows up later as a
confusing `ImportError: no module named 'asyncio'` from `mpsystemd.watchdog` or
`mpsystemd.signals`. Keep `.frozen` first:

```sh
MICROPYPATH=".frozen:/usr/lib/myservice" micropython main.py
```

### Other things worth knowing

- `sun_path` is 108 bytes. A filesystem socket path may be at most 107 bytes because of
  its NUL terminator, and an abstract name at most 107 characters after the `@`. A long
  temporary directory name is enough to run into this, which is why
  `FakeNotifyListener` uses the abstract namespace by default.
- A `NOTIFY_SOCKET` value beginning with `@` selects the Linux abstract namespace and
  is encoded as a leading NUL byte inside `sun_path`.
- The unix port has no `os.getpid`. `WATCHDOG_PID` and `LISTEN_PID` are compared against
  the value from libc `getpid`.
- Building a large journal entry costs a few multiples of its size in heap on
  MicroPython, since the value is encoded and then joined. A 512 KB message can run the
  unix port's heap out.
- Assignment values must not hold a newline, since that would start a new assignment.
  `notify()` raises `ValueError` rather than sending it.
- `booted()` reports that the machine booted with systemd. It does not report that this
  process is supervised by it. A container started by something else on a systemd host
  still sees `/run/systemd/system`.

## Why `mpsystemd`

- `systemd` is taken. `python-systemd` installs a package under exactly that name, and
  claiming it would mean two distributions fighting over the same directory in
  `site-packages`.
- `sdnotify` is taken on PyPI, and would understate a package that also does the
  journal, socket activation and signals.
- `usystemd` would follow the `u`-prefix convention MicroPython retired in v1.21, when
  `uasyncio` became `asyncio`.
- `mpsystemd` pairs obviously with the distribution name `micropython-systemd`, is a
  valid identifier, and collides with nothing.

The distribution is `micropython-systemd` on PyPI, and the import name is `mpsystemd`
on both runtimes.

## Layout

Importing `mpsystemd` gets the notification protocol, socket activation and the system
checks, and pulls in no asyncio. The asyncio helpers and the journal are submodules, so
nothing is imported that is not used.

| Module | Holds |
| --- | --- |
| `mpsystemd` | the flat public API |
| `mpsystemd.daemon` | the notification protocol and `Notifier` |
| `mpsystemd.activation` | socket activation |
| `mpsystemd.watchdog` | the asyncio watchdog task |
| `mpsystemd.signals` | `SignalWatcher` and the signal constants |
| `mpsystemd.journal` | journal submission and the `logging` handler |
| `mpsystemd.testing` | a fake listener and socket activation setup for tests |
| `mpsystemd.errors` | the exception types |

## Tests

The unit tests need no systemd and run under both runtimes:

```sh
python3 tests/run_tests.py
MICROPYPATH=".frozen:.:tests" micropython tests/run_tests.py
```

They assert real behaviour rather than shapes: notifications go over a real bound
socket, socket activation runs on real descriptors duplicated onto 3, signals are sent
to the test process and read back, descriptors are passed over `SCM_RIGHTS` and then
written through to prove they are the same descriptors, and the journal's memfd
transport is checked by reading the received descriptor back and confirming it is
sealed against writing.

Two of them report SKIP where a runtime cannot take part: comparing the signal
constants against the `signal` module, and the `logging` handler when `logging` is not
installed on MicroPython.

The integration tests need a running systemd and a service manager the caller can use:

```sh
python3 tests/integration/run_integration.py
python3 tests/integration/run_integration.py /path/to/micropython
sudo -E MPSYSTEMD_IT_SCOPE=system python3 tests/integration/run_integration.py
```

They start transient units with `systemd-run` and check that a `Type=notify` unit
becomes active, that `STATUS=` reaches `systemctl show`, that `FDSTORE=1` puts a
descriptor in the store, that `BARRIER=1` is acknowledged, that a unit under
`WatchdogSec=2s` survives four seconds and then fails with `Result=watchdog` once the
pings stop, that `systemctl reload` completes against a `Type=notify-reload` unit, and
that submitted entries come back out of `journalctl` intact including a multi line
message and a 256 KB one. The driver reports SKIP and exits 0 where systemd is not
usable, so CI can call it unconditionally.

## Licence

MIT. See [LICENSE](LICENSE).
