"""
Where PalmSentinel's files live -- in a source checkout and in a frozen build.

Two locations, deliberately different:

* **Read-only assets** -- ``templates/``, ``static/`` and the bundled demo
  orthomosaic -- sit beside the code.  Frozen, PyInstaller unpacks them into
  ``sys._MEIPASS``, which is a fresh temporary directory on every launch.
* **Writable state** -- the imagery library and anything uploaded into it --
  sits beside the executable, or under ``%LOCALAPPDATA%`` when that location is
  not writable (an install under ``Program Files``, or a read-only share).

Keeping these apart is the point.  A frozen build that writes its library into
``_MEIPASS`` loses every upload when the process exits, and one that only ever
reads ``_MEIPASS`` shows an empty library to a user whose own imagery is sitting
next to the exe.  One module owns that decision so nothing else has to ask
whether it is frozen.

The previous version had no such split: its launcher required a system Python
and its packaged spec omitted ``data/`` entirely, so the same gap produced two
different failures depending on how it was started.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Optional

APP_NAME = "PalmSentinelV2"

#: The one orthomosaic that ships inside a build.  Everything else in ``data/``
#: belongs to whoever is running the tool and is never packaged.
DEMO_ASSET_NAME = "demo_palm_estate.jpg"

#: Environment override, useful for tests and for keeping a library elsewhere.
DATA_DIR_ENV = "PALMSENTINEL_DATA_DIR"


def is_frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def asset_root() -> str:
    """
    Directory containing ``templates/``, ``static/`` and the bundled demo image.

    Frozen, this is ``sys._MEIPASS``.  From source it is the repository root,
    which is where this file lives.
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def bundled_demo_image() -> Optional[str]:
    """Path to the packaged demo orthomosaic, or ``None`` if it is not there."""
    candidate = os.path.join(asset_root(), "data", DEMO_ASSET_NAME)
    return candidate if os.path.isfile(candidate) else None


def _is_writable(directory: str) -> bool:
    """Whether we can actually create a file in ``directory``."""
    try:
        os.makedirs(directory, exist_ok=True)
        handle, probe = tempfile.mkstemp(prefix=".write-probe-", dir=directory)
        os.close(handle)
        os.unlink(probe)
        return True
    except OSError:
        return False


def _fallback_directory() -> str:
    """A library location that is writable even for a read-only install."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "data")


def data_dir(override: Optional[str] = None) -> str:
    """
    Writable directory holding the imagery library.

    The order is: an explicit argument, then ``PALMSENTINEL_DATA_DIR``, then --
    frozen -- a ``data`` folder beside the executable, then the per-user
    fallback.  From source it is simply the checkout's own ``data/``.
    """
    if override:
        resolved = os.path.abspath(override)
    else:
        from_env = os.environ.get(DATA_DIR_ENV)
        if from_env:
            resolved = os.path.abspath(from_env)
        elif is_frozen():
            beside_exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
            resolved = beside_exe if _is_writable(beside_exe) else _fallback_directory()
        else:
            resolved = os.path.join(asset_root(), "data")
    os.makedirs(resolved, exist_ok=True)
    return resolved


def seed_demo_image(directory: str) -> Optional[str]:
    """
    Make the bundled demo available in the library, once.

    A packaged build reads its demo from the (temporary) bundle, so without this
    the Library dialog would list nothing at all on a first run.  Copying it into
    the writable library keeps the read-only asset read-only while making it
    selectable like any other flight.

    The previous version's packaged build shipped no imagery whatsoever, which is
    why its interface came up with an empty library.
    """
    source = bundled_demo_image()
    if source is None:
        return None

    os.makedirs(directory, exist_ok=True)
    destination = os.path.join(directory, DEMO_ASSET_NAME)
    if os.path.exists(destination):
        return destination
    try:
        shutil.copyfile(source, destination)
        return destination
    except OSError:
        # A read-only library still works: point the loader at the bundled copy.
        return source


def describe() -> str:
    """One-line layout summary, printed at startup so it is never a mystery."""
    where = "packaged build" if is_frozen() else "source checkout"
    return f"{where}; assets {asset_root()}; library {data_dir()}"
