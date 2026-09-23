"""
PalmSentinel V2 -- web entry point.

    python app.py                serve on http://127.0.0.1:5000
    python app.py --port 5001    serve on exactly that port
    python desktop_app.py        serve inside a native desktop window

The server binds to the loopback interface only.  Nothing is sent off this
computer: the vision pipeline, the census and the exports are all local.

If 5000 is already taken -- by another copy of this application, or by anything
else -- the next free port is used and the port that was taken is named, so two
instances never end up sharing one.  An explicit ``--port`` means that port and
no other: if it is taken, this stops and says so rather than moving.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import ports

from web import create_app

app = create_app()


def main(argv: list[str]) -> int:
    store = app.extensions["palmsentinel_store"]
    try:
        claimed = ports.claim(ports.requested_port(argv))
    except ports.PortUnavailable as exc:
        print(f"[PalmSentinel] {exc}", file=sys.stderr)
        return 1

    print()
    print("  PalmSentinel V2")
    print("  " + "-" * 58)
    print(f"  image   : {store.describe()}")
    print(f"  url     : {claimed.url}")
    print(f"  data    : {store.data_dir}")
    print("  " + "-" * 58)
    if claimed.shifted:
        # Say it plainly: this is the line that stops a second copy from being a
        # silent second listener on the first one's port.
        print(f"  {claimed.announce().removeprefix('[PalmSentinel] ')}")
    print("  Local only. Imagery never leaves this computer.")
    print()
    # threaded=True so viewport crops can be served while a census is running.
    app.run(host="127.0.0.1", port=claimed.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
