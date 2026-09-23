"""
PalmSentinel V2 -- native desktop window.

    python desktop_app.py                    open the desktop window
    python desktop_app.py --check            verify the desktop stack and exit
    python desktop_app.py --selftest         drive the whole census headlessly
    python desktop_app.py --port 5123        pin the HTTP port a browser can attach to

The window is Edge WebView2 over a local server.  Nothing is transmitted
anywhere: the vision pipeline, the census and every export run on this computer.

Three deliberate differences from the previous version's desktop shell:

* **Failures are stated, not swallowed.**  A missing WebView2 runtime or a
  missing Python package produces a message naming the missing piece and the
  command that fixes it.  The previous launcher searched for a system Python,
  assumed five packages were installed in it, and told the user nothing when they
  were not.
* **--selftest runs inside the shipped binary.**  It exercises the real HTTP
  surface against the real bundled assets -- template rendering, the packaged
  static files, the packaged demo orthomosaic, a full census and a CSV export --
  so a build can be checked without a human clicking anything.  A packaged app
  that boots but cannot find its own imagery fails here.
* **The port is pinnable.**  `--port` lets a second window, a browser, or a
  verification script attach to the same server instead of racing for 5000.
"""

from __future__ import annotations

import os
import sys
import threading
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import paths

WINDOW_TITLE = "PalmSentinel V2"
DEFAULT_PORT = None  # None -> let the windowing layer pick a free port.

#: The verified dev-path result for the bundled demo orthomosaic, which is
#: 1.0000 ha at 4 cm/px assessed against the mature 9 m standard.  --selftest
#: asserts the shipped build reproduces it exactly.
EXPECTED_DEMO_PALMS = 140
EXPECTED_DEMO_SPH = 140.0


def _require_webview():
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


def _build_app():
    from web import create_app

    return create_app(preload=False)


def _start_preload(app) -> None:
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


# --------------------------------------------------------------------------
# --check  : is the desktop stack present at all?
# --------------------------------------------------------------------------


def _check() -> int:
    """Headless verification used by the launcher and by packaged --selftest."""
    print(f"[PalmSentinel] layout: {paths.describe()}")
    try:
        _require_webview()
        import bottle  # noqa: F401  (pywebview's WSGI server needs it)

        app = _build_app()
        routes = sorted(rule.rule for rule in app.url_map.iter_rules())
        assets = paths.asset_root()
        missing = [
            name
            for name in ("templates/index.html", "static/css/app.css")
            if not os.path.exists(os.path.join(assets, *name.split("/")))
        ]
        if missing:
            print(f"[PalmSentinel] Desktop stack FAILED: missing assets {missing}", file=sys.stderr)
            return 1
        print(f"[PalmSentinel] Desktop stack OK -- {len(routes)} routes, assets present")
        return 0
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[PalmSentinel] Desktop stack FAILED: {exc}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# --selftest : the shipped build proves itself, without a window
# --------------------------------------------------------------------------


def _selftest(report_path: str | None) -> int:
    """
    Drive a census and an export through the application's own HTTP surface.

    This runs in-process against the same app object the window renders, so it
    covers the parts a packaged build most often gets wrong: template discovery,
    the packaged static directory, the packaged demo orthomosaic, and the
    write path for exports.
    """
    lines: list[str] = []

    def log(text: str = "") -> None:
        lines.append(text)
        print(text)

    ok = True
    log("PalmSentinel V2 -- packaged self-test")
    log(f"frozen      : {paths.is_frozen()}")
    log(f"executable  : {sys.executable}")
    log(f"assets      : {paths.asset_root()}")
    log(f"library     : {paths.data_dir()}")
    log(f"demo asset  : {paths.bundled_demo_image()}")
    log()

    try:
        app = _build_app()
        client = app.test_client()

        # 1. The UI itself: a template rendered from the packaged templates dir.
        index = client.get("/")
        body = index.get_data(as_text=True)
        log(f"GET /                    -> {index.status_code}, {len(body)} bytes")
        if index.status_code != 200 or "PalmSentinel" not in body:
            raise AssertionError("the index template did not render")

        # 2. A packaged static asset.
        css = client.get("/static/css/app.css")
        log(f"GET /static/css/app.css  -> {css.status_code}, {len(css.get_data())} bytes")
        if css.status_code != 200 or not css.get_data():
            raise AssertionError("the packaged stylesheet was not served")

        # 3. The bundled demo orthomosaic, loaded the way the UI loads it.
        state = client.get("/api/state").get_json()
        demo = paths.bundled_demo_image()
        if demo is None:
            raise AssertionError("no demo orthomosaic is packaged")
        loaded = client.post("/api/image", json={"path": demo})
        info = loaded.get_json()
        log(
            "POST /api/image           -> "
            f"{loaded.status_code}, {info.get('image', {}).get('filename')} "
            f"{info.get('image', {}).get('width')}x{info.get('image', {}).get('height')} "
            f"@ {info.get('image', {}).get('gsd_cm_per_px')} cm/px"
        )
        if loaded.status_code != 200:
            raise AssertionError(f"could not load the packaged demo image: {info}")

        preview = client.get("/api/preview")
        log(f"GET /api/preview         -> {preview.status_code}, {len(preview.get_data())} bytes")
        if preview.status_code != 200:
            raise AssertionError("the preview raster was not served")

        # 4. The census, compared against the verified dev-path result.
        census = client.post("/api/census", json={})
        result = census.get_json()
        log(
            f"POST /api/census          -> {census.status_code}, "
            f"{result.get('total_palms')} palms, {result.get('sph')} SPH, "
            f"{result.get('area_ha')} ha, band {result.get('sph_band', {}).get('key')}"
        )
        log(
            f"                            closest pair "
            f"{result.get('quality', {}).get('closest_pair_px')} px vs "
            f"{result.get('quality', {}).get('required_spacing_px')} px required"
        )
        if census.status_code != 200:
            raise AssertionError(f"the census failed: {result}")
        if result["total_palms"] != EXPECTED_DEMO_PALMS or abs(result["sph"] - EXPECTED_DEMO_SPH) > 0.05:
            raise AssertionError(
                "the census disagrees with the verified result: "
                f"{result['total_palms']} palms / {result['sph']} SPH, expected "
                f"{EXPECTED_DEMO_PALMS} / {EXPECTED_DEMO_SPH}"
            )

        # 5. An export, written and read back.
        csv = client.post("/api/export/csv", json={"block": "Blok-Selftest"})
        text = csv.get_data(as_text=True)
        data_rows = [row for row in text.splitlines() if row and not row.startswith("#")]
        log(
            f"POST /api/export/csv      -> {csv.status_code}, "
            f"{len(text)} bytes, {len(data_rows) - 1} palm rows"
        )
        if csv.status_code != 200:
            raise AssertionError("the CSV export failed")
        if len(data_rows) - 1 != result["total_palms"]:
            raise AssertionError("the CSV export does not contain every palm")

        geo = client.post("/api/export/geojson", json={"block": "Blok-Selftest"})
        log(f"POST /api/export/geojson  -> {geo.status_code}, {len(geo.get_data())} bytes")
        if geo.status_code != 200:
            raise AssertionError("the GeoJSON export failed")

        annotate = client.post("/api/export/annotated", json={"block": "Blok-Selftest"})
        log(f"POST /api/export/annotated-> {annotate.status_code}, {len(annotate.get_data())} bytes")
        if annotate.status_code != 200:
            raise AssertionError("the annotated export failed")

        log()
        log(f"RESULT: PASS -- {result['total_palms']} palms / {result['sph']} SPH "
            f"matched the dev path; four exports produced output.")
    except Exception as exc:
        ok = False
        log()
        log(f"RESULT: FAIL -- {type(exc).__name__}: {exc}")

    if report_path:
        _write_report(report_path, lines)

    return 0 if ok else 1


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------


def _disable_page_zoom(webview) -> None:
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


def _parse_port(argv: list[str]) -> int | None:
    if "--port" not in argv:
        return DEFAULT_PORT
    try:
        return int(argv[argv.index("--port") + 1])
    except (IndexError, ValueError):
        raise SystemExit("[PalmSentinel] --port needs a number, e.g. --port 5123")


def main(argv: list[str]) -> int:
    port = _parse_port(argv)
    webview = _require_webview()
    _disable_page_zoom(webview)

    app = _build_app()
    _start_preload(app)

    print(f"[PalmSentinel] layout: {paths.describe()}")
    if port:
        print(f"[PalmSentinel] serving this session on http://127.0.0.1:{port}")

    webview.create_window(
        title=WINDOW_TITLE,
        url=app,
        width=1440,
        height=900,
        min_size=(1080, 680),
        background_color="#0E1013",
        text_select=True,
        zoomable=False,
        confirm_close=False,
        # The port belongs here, not on start(): a WSGI application gets its own
        # server created per window, and start()'s http_port only configures the
        # *global* server, which a callable url never uses.  Passing it there
        # silently left the port unpinned.
        http_port=port,
    )

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


# --------------------------------------------------------------------------
# --selftest --window : the shipped build proves itself *through its own UI*
# --------------------------------------------------------------------------


def _window_selftest(report_path: str | None, port: int | None) -> int:
    """
    Open the real window and drive the real interface inside it.

    The headless self-test proves the server, the packaged assets and the
    pipeline.  This proves the part none of that can reach: that Edge WebView2
    renders the packaged page, that the client script boots, and that pressing
    the census button in that window produces the same numbers as the dev path.

    Everything below is read back out of the live DOM through evaluate_js, so the
    evidence is the window's own state rather than this process's opinion of it.
    """
    webview = _require_webview()
    _disable_page_zoom(webview)

    app = _build_app()
    _start_preload(app)
    lines: list[str] = []
    findings: dict[str, object] = {}

    def log(text: str = "") -> None:
        lines.append(text)
        print(text)

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=app,
        width=1440,
        height=900,
        min_size=(1080, 680),
        background_color="#0E1013",
        text_select=True,
        zoomable=False,
        confirm_close=False,
        http_port=port,
    )

    # One budget for the whole drive phase, so a failure anywhere costs seconds
    # rather than minutes per step.
    budget = [time.monotonic() + 120.0]

    def wait_for(script: str, ready):
        """Poll the live DOM until `ready(value)` holds, or the budget runs out."""
        value = None
        while time.monotonic() < budget[0]:
            try:
                value = window.evaluate_js(script)
            except Exception:
                value = None
            if ready(value):
                return value
            time.sleep(0.4)
        return value

    def drive() -> None:
        try:
            window.events.loaded.wait(60)
            findings["title"] = window.evaluate_js("document.title")
            findings["url"] = window.evaluate_js("window.location.href")

            # The demo mosaic has to arrive from the packaged bundle before the
            # header stops saying that nothing is loaded.
            findings["filename"] = wait_for(
                "document.getElementById('file-name').textContent.trim()",
                lambda v: isinstance(v, str) and v and "No orthomosaic" not in v,
            )
            findings["file_meta"] = window.evaluate_js(
                "document.getElementById('file-meta').textContent.trim()"
            )

            # Press the real button, then read the real result panel.
            window.evaluate_js("document.getElementById('btn-run').click()")
            wait_for(
                "document.getElementById('results').hidden",
                lambda v: v is False,
            )
            wait_for(
                "document.getElementById('k-palms').textContent.trim()",
                lambda v: isinstance(v, str) and v not in ("", "0"),
            )
            findings["palms"] = window.evaluate_js(
                "document.getElementById('k-palms').textContent.trim()"
            )
            findings["area"] = window.evaluate_js(
                "document.getElementById('k-area').textContent.trim()"
            )
            findings["sph"] = window.evaluate_js(
                "document.getElementById('k-sph').textContent.trim()"
            )
            findings["band"] = window.evaluate_js(
                "document.getElementById('k-band').textContent.trim()"
            )
            findings["checks"] = window.evaluate_js(
                "Array.from(document.querySelectorAll('#quality-checks li')).map(function(n)"
                "{return n.className + ': ' + n.textContent;}).join(' | ')"
            )
            findings["rows"] = window.evaluate_js(
                "document.querySelectorAll('#palm-rows tr').length"
            )

            # The CSV export, through the same endpoint the button posts to and
            # fetched by the page itself rather than by this process.
            window.evaluate_js(
                "window.__psv2Export = null;"
                "fetch('/api/export/csv', {method: 'POST',"
                "  headers: {'Content-Type': 'application/json'},"
                "  body: JSON.stringify({block: 'Blok-Selftest'})})"
                ".then(function(r){return r.text().then(function(t){"
                "  window.__psv2Export = {status: r.status, bytes: t.length,"
                "    rows: t.split('\\n').filter(function(l){return l && l[0] !== '#';}).length - 1,"
                "    header: t.split('\\n')[0]};});});"
            )
            exported = wait_for(
                "JSON.stringify(window.__psv2Export)",
                lambda v: isinstance(v, str) and v not in ("null", "", None),
            )
            findings["export"] = exported
        except Exception as exc:  # pragma: no cover - depends on the desktop
            findings["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            time.sleep(0.5)
            try:
                window.destroy()
            except Exception:
                pass

    try:
        webview.start(drive, gui="edgechromium", debug=False)
    except Exception as exc:
        log(f"FAIL -- could not start the desktop window: {exc}")
        if report_path:
            _write_report(report_path, lines)
        return 1

    log("PalmSentinel V2 -- window self-test")
    log(f"frozen      : {paths.is_frozen()}")
    log(f"project     : {WINDOW_TITLE}")
    log()
    log(f"document.title          : {findings.get('title')!r}")
    log(f"window.location.href    : {findings.get('url')!r}")
    log(f"header file name        : {findings.get('filename')!r}")
    log(f"header file meta        : {findings.get('file_meta')!r}")
    log(f"palms (rendered)        : {findings.get('palms')!r}")
    log(f"area  (rendered)        : {findings.get('area')!r}")
    log(f"SPH   (rendered)        : {findings.get('sph')!r}")
    log(f"band  (rendered)        : {findings.get('band')!r}")
    log(f"palm table rows         : {findings.get('rows')!r}")
    log(f"quality checks          : {findings.get('checks')!r}")
    log(f"CSV export (from page)  : {findings.get('export')!r}")
    log()

    problems: list[str] = []
    if findings.get("error"):
        problems.append(str(findings["error"]))
    if WINDOW_TITLE not in str(findings.get("title") or ""):
        problems.append("the window did not render the application page")
    if findings.get("filename") in (None, "", "No orthomosaic loaded"):
        problems.append("the packaged demo orthomosaic never appeared in the header")
    if str(findings.get("palms")) != f"{EXPECTED_DEMO_PALMS:,}" and findings.get("palms") != EXPECTED_DEMO_PALMS:
        problems.append(
            f"the window rendered {findings.get('palms')!r} palms, expected {EXPECTED_DEMO_PALMS}"
        )
    if findings.get("sph") is None or abs(float(findings["sph"]) - EXPECTED_DEMO_SPH) > 0.05:
        problems.append(f"the window rendered {findings.get('sph')!r} SPH, expected {EXPECTED_DEMO_SPH}")
    export = findings.get("export")
    if not export or '"status":200' not in str(export).replace(" ", ""):
        problems.append("the CSV export from the window did not return 200")

    if problems:
        for problem in problems:
            log(f"RESULT: FAIL -- {problem}")
    else:
        log(
            f"RESULT: PASS -- the window rendered {findings['palms']} palms / {findings['sph']} SPH "
            f"over {findings['area']}, and the page exported the CSV itself."
        )

    if report_path:
        _write_report(report_path, lines)
    return 1 if problems else 0


def _write_report(report_path: str, lines: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"[PalmSentinel] report written to {report_path}")
    except OSError as exc:
        print(f"[PalmSentinel] could not write the report: {exc}", file=sys.stderr)


def _report_argument(argv: list[str]) -> str | None:
    for flag in ("--report",):
        if flag in argv:
            try:
                return argv[argv.index(flag) + 1]
            except IndexError:
                return None
    return None


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0] in ("--check", "--test", "-v", "--version"):
        raise SystemExit(_check())
    if arguments and arguments[0] == "--selftest":
        if "--window" in arguments:
            raise SystemExit(
                _window_selftest(_report_argument(arguments), _parse_port(arguments))
            )
        raise SystemExit(_selftest(_report_argument(arguments)))
    raise SystemExit(main(arguments))
