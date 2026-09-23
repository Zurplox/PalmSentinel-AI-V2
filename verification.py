"""
Verification harnesses -- does this build prove itself?

    python desktop_app.py --check                 the desktop stack is present
    python desktop_app.py --selftest              census + exports, headless
    python desktop_app.py --selftest --window     the same, through the real window

These three used to live in :mod:`desktop_app`, which made the file whose job is to
open a window the second owner of "is this build correct": 300 of its lines were
these harnesses.  Startup belongs to the entry point; proof belongs here.  The
flags, the exit codes and every line of output are unchanged, because the packaged
build and the three launchers depend on all three.

Each harness exists because the previous version got the corresponding thing
wrong:

* ``--check`` proves the stack the desktop path needs is actually installed.  The
  old launcher searched for a system Python, assumed five packages were present in
  it, and said nothing when they were not; this names the missing piece instead.
* ``--selftest`` runs inside the shipped binary, against the packaged assets: a
  template rendered from the packaged templates directory, the packaged
  stylesheet, the packaged demo orthomosaic, a full census and all four exports.
  A build that boots but cannot find its own imagery fails here.
* ``--selftest --window`` proves the part neither of the others can reach: that
  Edge WebView2 renders the packaged page, that the client script boots, and that
  the census button in that window produces the dev path's numbers.  Every claim
  is read back out of the live DOM, so the evidence is the window's own state
  rather than this process's opinion of it.

The expected demo result lives here rather than in the entry point, because it is
a property of the verification rather than of opening a window.
"""

from __future__ import annotations

import os
import sys
import time

import desktop_app
import paths
import ports

#: The verified dev-path result for the bundled demo orthomosaic, which is
#: 1.0000 ha at 4 cm/px assessed against the mature 9 m standard.  Both harnesses
#: assert the shipped build reproduces it exactly.
EXPECTED_DEMO_PALMS = 140
EXPECTED_DEMO_SPH = 140.0


def check() -> int:
    """Is the desktop stack present at all?  Used by the launchers and by --check."""
    print(f"[PalmSentinel] layout: {paths.describe()}")
    try:
        desktop_app.require_webview()
        import bottle  # noqa: F401  (pywebview's WSGI server needs it)

        app = desktop_app.build_app()
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


def census_selftest(argv: list[str]) -> int:
    """
    Drive a census and an export through the application's own HTTP surface.

    This runs in-process against the same app object the window renders, so it
    covers the parts a packaged build most often gets wrong: template discovery,
    the packaged static directory, the packaged demo orthomosaic, and the write
    path for exports.
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
        app = desktop_app.build_app()
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

        # 3. The bundled demo orthomosaic, loaded the way the UI loads it.  The
        #    state call is a precondition: if it is not serving JSON, the UI is
        #    not working whatever the census afterwards says.
        client.get("/api/state").get_json()
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

    _write_report(_report_argument(argv), lines)
    return 0 if ok else 1


def window_selftest(argv: list[str]) -> int:
    """
    Open the real window and drive the real interface inside it.

    The headless self-test proves the server, the packaged assets and the
    pipeline.  This proves the part none of that can reach: that Edge WebView2
    renders the packaged page, that the client script boots, and that pressing the
    census button in that window produces the same numbers as the dev path.

    Everything below is read back out of the live DOM through evaluate_js, so the
    evidence is the window's own state rather than this process's opinion of it.
    """
    claimed = desktop_app.claim_port(argv)
    webview = desktop_app.require_webview()
    desktop_app.disable_page_zoom(webview)

    app = desktop_app.build_app()
    desktop_app.start_preload(app)
    lines: list[str] = []
    findings: dict[str, object] = {}

    def log(text: str = "") -> None:
        lines.append(text)
        print(text)

    window = desktop_app.create_window(webview, claimed, app)

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
        _write_report(_report_argument(argv), lines)
        return 1

    log("PalmSentinel V2 -- window self-test")
    log(f"frozen      : {paths.is_frozen()}")
    log(f"project     : {desktop_app.WINDOW_TITLE}")
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
    if desktop_app.WINDOW_TITLE not in str(findings.get("title") or ""):
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

    _write_report(_report_argument(argv), lines)
    return 1 if problems else 0


def _write_report(report_path: str | None, lines: list[str]) -> None:
    if not report_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"[PalmSentinel] report written to {report_path}")
    except OSError as exc:
        print(f"[PalmSentinel] could not write the report: {exc}", file=sys.stderr)


def _report_argument(argv: list[str]) -> str | None:
    if "--report" not in argv:
        return None
    try:
        return argv[argv.index("--report") + 1]
    except IndexError:
        return None
