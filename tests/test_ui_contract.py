"""
Contracts between the browser client and this project's own files.

These are the bugs that pass every Python test because nothing joins the two
sides: the client looks up a DOM id the markup never defines, or reads a JSON key
the endpoint never emits.  Both happened, and both failed *silently* -- the map
simply drew nothing and the scale-bar text was never populated, with no exception
anywhere.  Nothing here needs a browser: each check compares two independent
sources (the JavaScript against the markup, the emitted payload against the keys
the client reads) so a rename on either side fails the suite.
"""

from __future__ import annotations

import os
import pathlib
import re
import unittest

from palmsentinel.agronomy import get_standard
from palmsentinel.pipeline import CensusConfig, run_census
from palmsentinel.scale import GroundScale

from synthetic import sparse_plantation

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "index.html"
JS_DIR = ROOT / "static" / "js"

#: Elements the client reaches for by id, in every form the code uses.
_ID_PATTERNS = (
    r"""\$\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""",
    r"""e\[['"]([A-Za-z0-9_-]+)['"]\]""",
    r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""",
)


def markup_ids() -> set:
    return set(re.findall(r'\bid="([^"]+)"', TEMPLATE.read_text(encoding="utf-8")))


def ids_read_by_client() -> set:
    ids = set()
    for path in JS_DIR.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        for pattern in _ID_PATTERNS:
            ids |= set(re.findall(pattern, source))
    return ids


class TestClientDomContract(unittest.TestCase):
    def test_every_id_the_client_reads_exists_in_the_markup(self):
        """`panels.updateScalebar` wrote to a `ro-scale` nobody had mounted."""
        missing = sorted(ids_read_by_client() - markup_ids())
        self.assertEqual(missing, [], f"client reads undefined element ids: {missing}")

    def test_the_mount_list_references_real_elements(self):
        """
        `mount()` bulk-resolves its ids, so a typo there yields `undefined` and
        the failure surfaces far away, on first use, as a TypeError in a frame
        callback.
        """
        mounted = self._mounted_ids()
        self.assertEqual(len(mounted), len(set(mounted)), "duplicate id in mount list")

        missing = sorted(set(mounted) - markup_ids())
        self.assertEqual(missing, [], f"mount() references undefined ids: {missing}")

    @staticmethod
    def _mounted_ids():
        """Every id `mount()` resolves, across all of its lookup lists."""
        source = (JS_DIR / "panels.js").read_text(encoding="utf-8")
        start = source.index("mount() {")
        end = source.index("#bind() {", start)
        return re.findall(r"'([A-Za-z0-9_-]+)'", source[start:end])

    def test_the_mount_list_covers_every_binding_the_panels_use(self):
        """
        `this.elements` is populated *only* by the mount list, so an id used as
        `e['name']` that the list forgot is `undefined`.  That is exactly how
        `ro-scale` broke: the element existed in the markup, so a markup-side
        check passed, while the scale bar threw a TypeError on every frame.
        """
        source = (JS_DIR / "panels.js").read_text(encoding="utf-8")
        mounted = set(self._mounted_ids())

        used = set(re.findall(r"e\[['\"]([A-Za-z0-9_-]+)['\"]\]", source))
        # `e.scalebar` style access, for bindings deliberately read as properties.
        used |= set(re.findall(r"\be\.([a-z][A-Za-z0-9_]*)\b", source))

        missing = sorted(used - mounted)
        self.assertEqual(
            missing, [], f"panels use bindings mount() never resolved: {missing}"
        )

    def test_every_hidden_overlay_is_reachable_from_the_client(self):
        """
        An overlay that carries `hidden` in the markup must be toggled by code,
        and the stylesheet must make `hidden` authoritative -- a component that
        sets `display` beats the UA attribute rule, which left four overlays on
        screen permanently.
        """
        markup = TEMPLATE.read_text(encoding="utf-8")
        toggled = {
            name
            for name in re.findall(r'id="([^"]+)"[^>]*\bhidden\b', markup)
        }
        # `hidden` may also be written before the id attribute.
        toggled |= {
            name
            for name in re.findall(r'<[^>]*\bhidden\b[^>]*\bid="([^"]+)"', markup)
        }

        source = "".join(p.read_text(encoding="utf-8") for p in JS_DIR.glob("*.js"))
        for name in sorted(toggled):
            self.assertIn(name, source, f"#{name} starts hidden and is never toggled")

        css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        guard = re.search(r"\[hidden\]\s*\{([^}]*)\}", css)
        self.assertIsNotNone(guard, "the stylesheet has no [hidden] rule")
        self.assertRegex(
            guard.group(1),
            r"display\s*:\s*none",
            "the [hidden] rule must disable display",
        )
        self.assertIn(
            "!important",
            guard.group(1),
            "component display rules outrank a bare [hidden] rule",
        )


class TestCensusPayloadContract(unittest.TestCase):
    """
    The palm keys the client actually reads.

    Gathered from the three places that consume them (the renderer, the spatial
    grid, the results table) rather than from the endpoint, so the assertion
    fails if either side renames a field.
    """

    CONSUMED_KEYS = {
        "id",
        "x_px",
        "y_px",
        "x_m",
        "y_m",
        "peak",
        "rosette_px",
        "rosette_m",
        "manual",
    }

    def setUp(self):
        self.scale = GroundScale(4.0)
        image, polygon, _ = sparse_plantation(self.scale)
        self.result = run_census(
            image,
            CensusConfig(
                standard=get_standard("mature"),
                scale=self.scale,
                polygon=polygon,
            ),
        )

    def test_payload_carries_exactly_the_keys_the_client_reads(self):
        from web.views import _palm_payload

        payload = _palm_payload(self.result)
        self.assertTrue(payload)
        self.assertEqual(
            set(payload[0]),
            self.CONSUMED_KEYS,
            "the palm payload and the client's field names have drifted apart",
        )

    def test_positions_are_full_resolution_pixels(self):
        """
        `x_px`/`y_px` are the same numbers the census routed on, so a click maps
        to a payload row with no conversion step.
        """
        from web.views import _palm_payload

        payload = _palm_payload(self.result)
        self.assertEqual(len(payload), self.result.total_palms)
        for row, palm in zip(payload, self.result.palms):
            self.assertAlmostEqual(row["x_px"], palm.x_px, places=2)
            self.assertAlmostEqual(row["y_px"], palm.y_px, places=2)
            self.assertAlmostEqual(row["x_m"], palm.x_m, places=3)
            self.assertAlmostEqual(row["y_m"], palm.y_m, places=3)


class TestRasterIdentityContract(unittest.TestCase):
    """
    The browser cannot be allowed to show one image's cached pixels for another.

    ``/api/preview`` is one URL for the whole session and is HTTP-cached for a
    day, so after any import the renderer could fetch the *previous* image's
    raster from the browser cache and paint it into the new image's (correct)
    extent -- the demo, being smaller than the preview cap, never exposed it;
    a real 100+ MP orthomosaic always did.  The fix keys every per-image URL by
    a content fingerprint the payload reports.  These tests keep both halves
    joined: the store must report one, and the client must carry it.
    """

    def _store_with(self, name, bgr):
        import os
        import shutil
        import tempfile

        import cv2

        tmp = tempfile.mkdtemp(prefix="psv2-store-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, name)
        cv2.imwrite(path, bgr)
        return path

    def test_distinct_images_report_distinct_fingerprints(self):
        import numpy as np

        from web.store import ImageStore

        red = self._store_with("red.jpg", np.full((64, 64, 3), (0, 0, 255), np.uint8))
        blue = self._store_with("blue.jpg", np.full((64, 64, 3), (255, 0, 0), np.uint8))
        store = ImageStore(os.path.dirname(red))
        first = store.load(red)
        second = store.load(blue)
        self.assertTrue(first.fingerprint)
        self.assertTrue(second.fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        # ...and both reach the payload the client reads.
        self.assertIn("fingerprint", first.to_dict())

    def test_fingerprint_survives_a_gsd_change(self):
        """set_gsd rebuilds ImageInfo; a dropped fingerprint would resurrect the bug."""
        import numpy as np

        from web.store import ImageStore

        red = self._store_with("red.jpg", np.full((64, 64, 3), (0, 0, 255), np.uint8))
        store = ImageStore(os.path.dirname(red))
        info = store.load(red)
        reinfo = store.set_gsd(9.0)
        self.assertEqual(info.fingerprint, reinfo.fingerprint)

    def test_the_client_keys_raster_urls_by_the_reported_fingerprint(self):
        api_js = (JS_DIR / "api.js").read_text(encoding="utf-8")
        render_js = (JS_DIR / "render.js").read_text(encoding="utf-8")

        def builder_source(name):
            """The body of one URL builder, so a drift in one cannot hide behind the others."""
            start = api_js.index(f"{name}(")
            end = api_js.index("},", start)
            return api_js[start:end]

        # Each per-image URL construction carries the key...
        for name in ("cropUrl", "sampleUrl", "previewUrl"):
            self.assertIn("fingerprint", builder_source(name), f"{name} stopped keying its URL by fingerprint")
        # ...all three load paths capture it...
        self.assertEqual(
            3,
            len(re.findall(r"fingerprint = body\.image\?\.fingerprint", api_js)),
            "state/loadFile/loadPath must all adopt the reported fingerprint",
        )
        # ...and the renderer asks for the keyed preview, not the bare URL.
        self.assertNotIn("loadImage('/api/preview')", render_js)
        self.assertIn("api.previewUrl()", render_js)


if __name__ == "__main__":
    unittest.main()
