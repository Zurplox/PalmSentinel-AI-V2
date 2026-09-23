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


if __name__ == "__main__":
    unittest.main()
