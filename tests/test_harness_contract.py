"""
The harness contract, held still.

Three flags make promises a downloader relies on.  ``--check`` says the desktop
stack is present; ``--selftest`` says the build can render its own template, serve
its own stylesheet, load its own orthomosaic, census it and write four exports;
``--selftest --window`` says the same through Edge WebView2, read back out of the
live DOM.  The three launchers and the packaged build call them, so what they
print is a contract rather than an implementation detail -- which is why the
transcripts are committed under ``tests/golden/`` and this module re-runs each
flag and fails on a single character of drift.

What this proves, and what it does not.  These are *recordings* of behaviour that
was verified by hand, not independent truth: they cannot tell you the census is
right (that is ``test_core.py`` and ``test_ground_truth.py``) or that the numbers
are defensible (``tests/ground_truth.py``).  They tell you that nobody changed the
promise without saying so.  An intended change is regenerated deliberately, and
the diff is the review:

    python tests/test_harness_contract.py --regenerate

Three placeholders keep a transcript committable, because it otherwise contains
text belonging to one machine: ``<REPO>`` for the checkout, ``<PYTHON>`` for the
interpreter running it, ``<PORT>`` for whichever port was free.  Everything else
-- every count, byte size, band and export figure -- is compared literally.

The flag vocabulary itself is declared in three places: ``desktop_app``'s
``VERIFICATION_FLAGS``, ``Launcher.cs``'s ``IsCheckMode`` (frozen by decision, see
``SYSTEM_LIVING_LOG_V2.md``) and the two module docstrings.  A test below fails if
they disagree, because the C# copy cannot be deleted and must not be allowed to
drift.  ``PALMSENTINEL_SKIP_WINDOW_TESTS=1`` skips the one test that opens a real
window, for a machine with no display or no WebView2 runtime.
"""

from __future__ import annotations

import difflib
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "golden"

if __package__ in (None, ""):  # run directly, as the docstring above invites
    sys.path.insert(0, str(ROOT))

#: transcript name -> the arguments that produce it
HARNESSES = {
    "check": ("--check",),
    "selftest": ("--selftest",),
    "window-selftest": ("--selftest", "--window"),
}

#: Generous: a loaded machine runs the census in seconds, and the window harness
#: waits on a real webview.
HARNESS_TIMEOUT = 420

SKIP_WINDOW = "PALMSENTINEL_SKIP_WINDOW_TESTS"


def normalize(text: str) -> str:
    """Collapse this machine's own text to placeholders, leaving the claims alone."""
    text = re.sub(re.escape(str(ROOT)), "<REPO>", text, flags=re.IGNORECASE)
    text = re.sub(re.escape(sys.executable), "<PYTHON>", text, flags=re.IGNORECASE)
    text = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<PORT>", text)
    return re.sub(r"— port \d+", "— port <PORT>", text)


def run_harness(name: str) -> "tuple[int, str]":
    """Run one flag the way a launcher does, and return its exit code and transcript."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "desktop_app.py"), *HARNESSES[name]],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=HARNESS_TIMEOUT,
    )
    return completed.returncode, normalize(completed.stdout)


class TestHarnessTranscripts(unittest.TestCase):
    """Each flag still prints exactly what it promised."""

    def _assert_matches(self, name: str) -> None:
        golden = GOLDEN / f"{name}.txt"
        self.assertTrue(golden.exists(), f"missing {golden}; regenerate with --regenerate")
        code, transcript = run_harness(name)
        self.assertEqual(code, 0, f"{name} exited {code}")
        expected = golden.read_text(encoding="utf-8")
        if transcript != expected:
            difference = "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    transcript.splitlines(keepends=True),
                    fromfile=f"tests/golden/{name}.txt",
                    tofile=f"{name} (now)",
                )
            )
            self.fail(f"{name} no longer prints what its golden transcript records:\n{difference}")

    def test_check_transcript_is_unchanged(self) -> None:
        self._assert_matches("check")

    def test_selftest_transcript_is_unchanged(self) -> None:
        self._assert_matches("selftest")

    @unittest.skipIf(os.environ.get(SKIP_WINDOW) == "1", f"{SKIP_WINDOW}=1")
    def test_window_selftest_transcript_is_unchanged(self) -> None:
        self._assert_matches("window-selftest")

    def test_the_three_aliases_mean_the_same_as_check(self) -> None:
        # The docstrings call them aliases, so this makes that a fact rather than
        # a description: each one has to produce the --check transcript exactly.
        expected = (GOLDEN / "check.txt").read_text(encoding="utf-8")
        for alias in ("--check", "--test", "-v", "--version"):
            with self.subTest(flag=alias):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "desktop_app.py"), alias],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=HARNESS_TIMEOUT,
                )
                self.assertEqual(completed.returncode, 0, f"{alias} exited non-zero")
                self.assertEqual(normalize(completed.stdout), expected)


class TestFlagVocabulary(unittest.TestCase):
    """
    The same list is declared three times, and only one copy is Python.

    ``Launcher.cs`` decides from its own copy whether a launch should be windowless,
    and it is frozen by decision -- so the duplicate cannot be deleted, only
    watched.  This is the watch.
    """

    def test_the_launcher_the_dispatch_and_the_docstrings_agree(self) -> None:
        import desktop_app
        import verification

        declared = set(desktop_app.VERIFICATION_FLAGS)
        self.assertEqual(len(declared), len(desktop_app.VERIFICATION_FLAGS), "a flag is listed twice")
        self.assertNotIn("--port", declared, "--port opens a window; it is not a verification flag")

        launcher = (ROOT / "Launcher.cs").read_text(encoding="utf-8")
        found = set(re.findall(r'"(-[a-z-]+)"', launcher))
        self.assertEqual(
            found,
            declared,
            "Launcher.cs's IsCheckMode and desktop_app.VERIFICATION_FLAGS no longer agree",
        )

        for module in (desktop_app, verification):
            for flag in sorted(declared):
                # Bounded on both sides, so `-v` cannot be satisfied by the `-v`
                # inside `--version` -- which is exactly what the substring check
                # this replaces could never fail for.
                self.assertRegex(
                    module.__doc__ or "",
                    rf"(?<![\w-]){re.escape(flag)}(?![\w-])",
                    f"{flag} is dispatched but not documented in {module.__name__}",
                )


class TestRefusalsAreVisible(unittest.TestCase):
    """A ``--port`` value that is not a port stops the launch, and says so."""

    def test_with_somewhere_to_print_it_prints_and_does_not_dial(self) -> None:
        # A dialog in a scripted launch is a hang: measured before this was fixed,
        # `desktop_app.py --port 0` was still alive after 25 s with a modal box on
        # screen, where `app.py --port 0` had exited 1 with the same sentence.
        import desktop_app

        stream = io.StringIO()
        dialogs: "list[str]" = []
        original_stream, original_dialog = desktop_app._output_stream, desktop_app._show_dialog
        desktop_app._output_stream = lambda: stream
        desktop_app._show_dialog = dialogs.append
        try:
            desktop_app._report_fatal("[PalmSentinel] 0 is not a usable port")
        finally:
            desktop_app._output_stream = original_stream
            desktop_app._show_dialog = original_dialog

        self.assertIn("not a usable port", stream.getvalue())
        self.assertEqual(dialogs, [], "a refusal nobody can dismiss is a hang")

    def test_with_nowhere_to_print_it_shows_a_dialog(self) -> None:
        # The double-clicked build: no console and nothing redirected, so a print
        # goes nowhere and the message would be invisible.
        import desktop_app

        dialogs: "list[str]" = []
        original_stream, original_dialog = desktop_app._output_stream, desktop_app._show_dialog
        desktop_app._output_stream = lambda: None
        desktop_app._show_dialog = dialogs.append
        try:
            desktop_app._report_fatal("[PalmSentinel] 0 is not a usable port")
        finally:
            desktop_app._output_stream = original_stream
            desktop_app._show_dialog = original_dialog

        self.assertEqual(dialogs, ["[PalmSentinel] 0 is not a usable port"])

    def test_a_bad_port_stops_the_desktop_entry_instead_of_waiting(self) -> None:
        # The defect this pins, measured through the real entry point: the process
        # used to print the sentence and then sit on a modal box -- alive at 25 s,
        # which is a hang to anything scripted.  Streams are what a terminal, a
        # launcher or a capture gives it, and with them it must stop.
        completed = subprocess.run(
            [sys.executable, str(ROOT / "desktop_app.py"), "--port", "0"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("not a usable port", completed.stdout + completed.stderr)

    def test_an_unusable_port_stops_the_launch_and_is_surfaced(self) -> None:
        import desktop_app

        surfaced: "list[str]" = []
        original = desktop_app._report_fatal
        desktop_app._report_fatal = surfaced.append  # no dialog can be opened here
        try:
            with self.assertRaises(SystemExit) as stopped:
                desktop_app.claim_port(["--port", "70000"])
        finally:
            desktop_app._report_fatal = original

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(len(surfaced), 1, "the refusal was not surfaced for a windowless launch")
        self.assertIn("70000", surfaced[0])
        self.assertIn("65535", surfaced[0])

    def test_a_taken_port_still_says_which_port_and_what_to_do(self) -> None:
        import desktop_app
        import ports

        surfaced: "list[str]" = []
        original_report, original_claim = desktop_app._report_fatal, ports.claim

        def unusable(*_args, **_kwargs):
            raise ports.PortUnavailable(5123)

        desktop_app._report_fatal = surfaced.append
        ports.claim = unusable
        try:
            with self.assertRaises(SystemExit) as stopped:
                desktop_app.claim_port(["--port", "5123"])
        finally:
            desktop_app._report_fatal = original_report
            ports.claim = original_claim

        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("5123", surfaced[0])
        self.assertIn("--port", surfaced[0])


def regenerate() -> int:
    """Rewrite the goldens from the current build, printing every difference."""
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in HARNESSES:
        if name == "window-selftest" and os.environ.get(SKIP_WINDOW) == "1":
            print(f"{name}: skipped ({SKIP_WINDOW}=1)")
            continue
        code, transcript = run_harness(name)
        path = GOLDEN / f"{name}.txt"
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        if before == transcript:
            print(f"{name}: unchanged (ran with exit {code})")
            continue
        path.write_text(transcript, encoding="utf-8", newline="\n")
        print(f"{name}: rewritten from a run that exited {code} -- review with git diff")
    return 0


if __name__ == "__main__":
    if "--regenerate" in sys.argv[1:]:
        raise SystemExit(regenerate())
    unittest.main()
