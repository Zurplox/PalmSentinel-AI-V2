"""
PalmSentinel V2 -- web entry point.

    python app.py            serve on http://127.0.0.1:5000
    python desktop_app.py    serve inside a native desktop window

The server binds to the loopback interface only.  Nothing is sent off this
computer: the vision pipeline, the census and the exports are all local.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from web import create_app

app = create_app()


def main() -> int:
    store = app.extensions["palmsentinel_store"]
    print()
    print("  PalmSentinel V2")
    print("  " + "-" * 58)
    print(f"  image   : {store.describe()}")
    print(f"  url     : http://127.0.0.1:5000")
    print(f"  data    : {store.data_dir}")
    print("  " + "-" * 58)
    print("  Local only. Imagery never leaves this computer.")
    print()
    # threaded=True so viewport crops can be served while a census is running.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
