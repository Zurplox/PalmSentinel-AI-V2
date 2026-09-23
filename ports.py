"""
Which port this instance serves on -- and the guarantee that two of them never
share one.

The defect this module exists to remove.  Both servers V2 can start inherit
``allow_reuse_address = True`` from ``http.server.HTTPServer``: Werkzeug's dev
server behind ``app.py``, and the ``wsgiref`` server pywebview runs for the
desktop window.  Both therefore set ``SO_REUSEADDR``, and on Windows that option
does not mean "reuse a port left in TIME_WAIT" the way it does on Unix -- it
means a *second* socket may bind a port another socket is already listening on.
So two launches both bound 127.0.0.1:5000, both reported success, and which
process answered any given request stopped being something the operator
controlled.  Three instances were observed listening on 5000 at once.

The rule here is the one a person can predict without reading any of this:

* The default port is 5000.  If it is taken, the next free port above it is
  used, and the port that was taken is named -- never a silent share.
* An explicit ``--port N`` is honoured exactly.  If N is taken that is an error
  naming N, not a step sideways.

Two instances cannot choose the same port even when they start in the same
instant, because a port is claimed by an atomic lock file before it is used.  The
claim is then confirmed with a real bind, so a port held by something that is not
PalmSentinel is passed over too.
"""

from __future__ import annotations

import atexit
import errno
import os
import socket
import tempfile
from dataclasses import dataclass
from typing import Optional, Sequence

#: The port everyone expects, and the one the README documents.
DEFAULT_BASE_PORT = 5000

#: The largest port a socket can bind.  Above this, ``bind`` raises
#: ``OverflowError`` rather than an ``OSError``, so ``--port`` validates the range
#: itself to keep the refusal a sentence.
PORT_LIMIT = 65535

#: How far above the base port to look before giving up.  Fifty consecutive busy
#: ports is not a busy machine, it is a mistake -- and silently walking to some
#: arbitrary high port would be worse than saying so.
SEARCH_WINDOW = 50

#: port -> the descriptor that keeps its lock file open for as long as we run.
_held: "dict[int, int]" = {}


class PortUnavailable(RuntimeError):
    """A port could not be claimed, and stepping sideways was not allowed."""

    def __init__(self, port: int, detail: Optional[str] = None) -> None:
        self.port = port
        super().__init__(
            detail
            or (
                f"port {port} is already in use. Start with a different one, "
                f"for example: --port {port + 1}"
            )
        )


@dataclass(frozen=True)
class Claim:
    """A port this process owns until it exits."""

    port: int
    base: int

    @property
    def shifted(self) -> bool:
        """Whether the base port was taken and a different one was used."""
        return self.port != self.base

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def announce(self) -> str:
        """One line saying which port this instance is on, and why."""
        if not self.shifted:
            return f"[PalmSentinel] serving on {self.url}"
        return (
            f"[PalmSentinel] port {self.base} is already in use; "
            f"serving on {self.url} instead"
        )


def requested_port(argv: Sequence[str]) -> Optional[int]:
    """
    Read ``--port N`` or ``--port=N`` out of a command line, or return ``None``.

    One reader, so ``app.py`` and ``desktop_app.py`` cannot disagree about what
    the flag means or how it fails: every unusable value is refused the same way,
    in a sentence rather than a traceback.

    The range is checked here rather than left to ``bind``, which raises
    ``OverflowError: port must be 0-65535`` for anything above the limit.  Port 0
    is refused with the rest: the operating system would assign an arbitrary free
    port, which is the opposite of what ``--port`` promises.  Without the flag the
    search behaviour is unchanged -- that is what claiming the base port upward is
    for.

    Both spellings are read because the other one is what a command line habit
    produces, and measured before this: ``app.py --port=5123`` was not recognised,
    was therefore ignored, and the app served on the shared default port 5000 while
    saying so -- the silent substitution this module exists to prevent.
    """
    tokens = list(argv)
    raw: Optional[str] = None
    for index, token in enumerate(tokens):
        if token == "--port":
            if index + 1 >= len(tokens):
                raise SystemExit("[PalmSentinel] --port needs a number, e.g. --port 5001")
            raw = tokens[index + 1]
            break
        if token.startswith("--port="):
            raw = token[len("--port="):]
            break
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit("[PalmSentinel] --port needs a number, e.g. --port 5001")
    if not 1 <= value <= PORT_LIMIT:
        raise SystemExit(
            f"[PalmSentinel] {value} is not a usable port; --port takes a number "
            f"between 1 and {PORT_LIMIT}, e.g. --port 5001"
        )
    return value


def _lock_path(port: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"palmsentinel-v2-{port}.lock")


def _acquire(port: int) -> Optional[int]:
    """
    Claim ``port`` for this process, or return ``None`` if another one has it.

    The lock file is created with ``O_EXCL``, which is atomic, so two instances
    starting at the same instant cannot both succeed.  Freshness needs no
    process-id check: while a live process holds the file open Windows refuses to
    let anyone delete it, and once that process is gone the deletion succeeds.  A
    crash therefore cannot leave a lock that blocks the port forever.
    """
    path = _lock_path(port)
    for attempt in (False, True):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if attempt:
                return None
            try:
                os.unlink(path)
            except OSError:
                # Held open by a live instance.
                return None
            continue
        except OSError:
            return None
        try:
            os.write(handle, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            pass
        _held[port] = handle
        return handle
    return None


def _is_free(port: int) -> bool:
    """
    Whether a plain listener can bind ``port`` right now.

    Deliberately *without* ``SO_REUSEADDR``: that flag is what lets a second
    socket bind a port another socket is already listening on, so a probe using
    it would answer the wrong question.  A plain bind cannot do that -- measured:
    against a running PalmSentinel server it fails with 10048, "only one usage of
    each socket address" -- so this is a real answer.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            return False
        raise
    finally:
        probe.close()


def release(claim: "Claim | int") -> None:
    """Give back a claimed port.  Called automatically when the process exits."""
    port = claim.port if isinstance(claim, Claim) else claim
    handle = _held.pop(port, None)
    if handle is not None:
        try:
            os.close(handle)
        except OSError:
            pass
    try:
        os.unlink(_lock_path(port))
    except OSError:
        pass


def claim(
    requested: Optional[int] = None,
    *,
    base: int = DEFAULT_BASE_PORT,
    window: int = SEARCH_WINDOW,
) -> Claim:
    """
    Claim a port.

    With ``requested`` set, that exact port or :class:`PortUnavailable`.  Without
    it, the first free port from ``base`` upward, which may be ``base`` itself.
    """
    if requested is not None:
        if _acquire(requested) is None or not _is_free(requested):
            release(requested)
            raise PortUnavailable(requested)
        return Claim(requested, requested)

    for port in range(base, base + window):
        if _acquire(port) is None:
            continue
        if _is_free(port):
            return Claim(port, base)
        # Somebody else has it.  Hold nothing and keep looking.
        release(port)

    raise PortUnavailable(
        base, f"every port from {base} to {base + window - 1} is in use"
    )


atexit.register(lambda: [release(port) for port in list(_held)])
