"""
PalmSentinel V2 -- native desktop window.

    python desktop_app.py                    open the desktop window
    python desktop_app.py --check            verify the desktop stack and exit
    python desktop_app.py --selftest         drive the whole census headlessly
    python desktop_app.py --port 5123        serve on exactly this port, or refuse

The window is Edge WebView2 over a local server.  Nothing is transmitted
anywhere: the vision pipeline, the census and every export run on this computer.

This module owns **startup** -- the window, its geometry, the server, the port and
the dependency preflight -- and nothing else.  Proving a build correct is a
separate job with a separate owner (:mod:`verification`), which is why the three
verification flags are dispatched to it from ``__main__`` and only the flag names
are known here.  Keeping the harnesses in this file made the entry point a second
owner of "is this build correct", and that is the kind of duplicated ownership
that lets the two drift apart.

Three deliberate differences from the previous version's desktop shell:

* **Failures are stated, not swallowed.**  A missing WebView2 runtime or a
  missing Python package produces a message naming the missing piece and the
  command that fixes it.  The previous launcher searched for a system Python,
  assumed five packages were installed in it, and told the user nothing when they
  were not.
* **The build can prove itself.**  ``--selftest`` and ``--selftest --window``
  exercise the real HTTP surface and the real window against the real bundled
  assets, so a shipped build can be checked without a human clicking anything.
  See :mod:`verification`.
* **The port cannot be shared.**  The default is 5000; if it is taken, the next
  free port is used and the port that was taken is named -- in the window title
  and on stdout -- so a second launch is visible instead of quietly becoming a
  second listener on the first one's socket.  `--port` pins an exact port and
  refuses rather than moving, because a pinned port that silently becomes a
  different port is worse than a refusal.
"""

from __future__ import annotations

import os
import sys
import threading

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import paths
import ports

WINDOW_TITLE = "PalmSentinel V2"

#: The window's geometry, in one place.  The shell and the window self-test must
#: open the same window, so neither may carry its own copy of these.
WINDOW_SIZE = (1440, 900)
WINDOW_MIN_SIZE = (1080, 680)
WINDOW_BACKGROUND = "#0E1013"


def require_webview():
    """
    Import pywebview, naming the missing dependency if it is absent.

    pywebview needs a second package before it can talk to a WebView2 control:
    it serves a WSGI application through its own Bottle-based server, so bottle
    is not optional for this application.  The previous version's dependency list
    named neither, so `pip install -r requirements.txt` produced a build that
    could not open a window.
    """
    try:
        import webview
        return webview
    except ImportError as exc:
        raise SystemExit(
            f"[PalmSentinel] Cannot open the desktop window: {exc}.\n"
            "             Install the desktop dependency with:\n"
            "                 pip install -r requirements.txt"
        ) from exc


def build_app():
    from web import create_app

    return create_app(preload=False)


def start_preload(app) -> None:
    """
    Decode the default orthomosaic on a background thread.

    The window must appear immediately, so the app is built without preloading
    and the decode happens behind it; the frontend polls /api/state and shows a
    real loading state until the image is ready.  Shared with the window
    self-test so that test exercises the same startup the user gets -- without
    it the header never leaves "No orthomosaic loaded".
    """
    store = app.extensions["palmsentinel_store"]

    def preload() -> None:
        default = store.default_image()
        if default:
            try:
                store.load(default)
            except Exception as exc:  # pragma: no cover - depends on the asset
                print(f"[PalmSentinel] Preload failed: {exc}")

    threading.Thread(target=preload, name="preload", daemon=True).start()


def window_title(claimed: ports.Claim) -> str:
    """
    Name the port in the title when it is not the expected one.

    The title bar is the only place a double-clicked window can show which port
    it ended up on, because the packaged build has no console to print to.
    """
    if not claimed.shifted:
        return WINDOW_TITLE
    return f"{WINDOW_TITLE} — port {claimed.port}"


def create_window(webview, claimed: ports.Claim, app):
    """The one place the window is configured, for the shell and for its test."""
    return webview.create_window(
        title=window_title(claimed),
        url=app,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        background_color=WINDOW_BACKGROUND,
        text_select=True,
        zoomable=False,
        confirm_close=False,
        # The port belongs here, not on start(): a WSGI application gets its own
        # server created per window, and start()'s http_port only configures the
        # *global* server, which a callable url never uses.  Passing it there
        # silently left the port unpinned.
        http_port=claimed.port,
    )


def disable_page_zoom(webview) -> None:
    """Stop WebView2 from consuming the wheel and pinch gestures as page zoom."""
    try:
        import webview.platforms.edgechromium as edge

        original = edge.EdgeChrome.on_webview_ready

        def patched(self, sender, args):  # type: ignore[no-untyped-def]
            original(self, sender, args)
            if getattr(args, "IsSuccess", False) and getattr(sender, "CoreWebView2", None):
                try:
                    settings = sender.CoreWebView2.Settings
                    settings.IsZoomControlEnabled = False
                    settings.AreBrowserAcceleratorKeysEnabled = False
                except Exception as exc:  # pragma: no cover - engine specific
                    print(f"[PalmSentinel] WebView2 zoom note: {exc}")

        edge.EdgeChrome.on_webview_ready = patched
    except Exception as exc:  # pragma: no cover - engine specific
        print(f"[PalmSentinel] WebView2 zoom patch unavailable: {exc}")


def _report_fatal(message: str) -> None:
    """
    Say something that stopped the launch, where a double-click can see it.

    A windowed process started from Explorer has no console, so anything on
    stderr goes nowhere.  That is the same reason the launcher writes
    ``launcher.log`` and shows a dialog instead of printing: a failure the user
    cannot see is a failure they will report as "it does nothing".
    """
    print(message, file=sys.stderr)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    except Exception:  # pragma: no cover - platform dependent
        pass


def claim_port(argv: list[str]) -> ports.Claim:
    """Claim the port this window will serve on, or stop with a visible message."""
    try:
        return ports.claim(ports.requested_port(argv))
    except ports.PortUnavailable as exc:
        _report_fatal(f"[PalmSentinel] {exc}")
        raise SystemExit(1)


def main(argv: list[str]) -> int:
    claimed = claim_port(argv)
    webview = require_webview()
    disable_page_zoom(webview)

    app = build_app()
    start_preload(app)

    print(f"[PalmSentinel] layout: {paths.describe()}")
    print(claimed.announce())

    create_window(webview, claimed, app)

    try:
        # The Edge backend is named explicitly rather than discovered: a silent
        # fall back to another toolkit would produce a different window than the
        # one this application is built and tested against.
        webview.start(gui="edgechromium", debug=False)
    except Exception as exc:
        print(
            "[PalmSentinel] Could not start the desktop window.\n"
            "             This needs the Microsoft Edge WebView2 runtime, which ships\n"
            "             with Windows 10/11 and with Microsoft Edge. If it has been\n"
            "             removed, install it from:\n"
            "             https://developer.microsoft.com/microsoft-edge/webview2/\n"
            f"             detail: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0] in ("--check", "--test", "-v", "--version"):
        import verification

        raise SystemExit(verification.check())
    if arguments and arguments[0] == "--selftest":
        import verification

        harness = verification.window_selftest if "--window" in arguments else verification.census_selftest
        raise SystemExit(harness(arguments))
    raise SystemExit(main(arguments))
