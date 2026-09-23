"""
The port claim.

The property that matters is not "the numbers look right" but "two instances can
never end up on one port", so the tests below actually hold sockets and actually
race two claims against each other rather than re-reading the implementation.
"""

from __future__ import annotations

import socket
import threading
import unittest

import ports


def _free_port() -> int:
    """An ephemeral port nothing is listening on, so tests never fight a real one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _free_run(length: int) -> int:
    """The base of a run of `length` consecutive free ports."""
    for _ in range(200):
        base = _free_port()
        held = []
        try:
            for offset in range(length):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", base + offset))
                held.append(sock)
        except OSError:
            continue
        finally:
            for sock in held:
                sock.close()
        return base
    raise AssertionError("no run of free ports on this machine")


class TestRequestedPort(unittest.TestCase):
    def test_absent_means_none(self) -> None:
        self.assertIsNone(ports.requested_port([]))
        self.assertIsNone(ports.requested_port(["--check", "--selftest"]))

    def test_reads_the_number_out_of_a_full_command_line(self) -> None:
        self.assertEqual(ports.requested_port(["--port", "5123"]), 5123)
        self.assertEqual(
            ports.requested_port(["--selftest", "--window", "--port", "5124"]), 5124
        )

    def test_a_missing_or_unparsable_number_stops_cleanly(self) -> None:
        # A traceback here would be the same class of defect as a silent share:
        # the operator asked for something specific and got noise.
        with self.assertRaises(SystemExit):
            ports.requested_port(["--port"])
        with self.assertRaises(SystemExit):
            ports.requested_port(["--port", "http"])


class TestClaim(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _free_port()
        self.claims: list = []

    def tearDown(self) -> None:
        for claimed in self.claims:
            ports.release(claimed)

    def _claim(self, *args, **kwargs):
        claimed = ports.claim(*args, **kwargs)
        self.claims.append(claimed)
        return claimed

    def _squatter(self, port: int, reuse: bool = True) -> None:
        """
        A listener on `port`, standing in for whoever else might have it.

        `reuse=True` reproduces the case that actually matters: Werkzeug and
        pywebview both set SO_REUSEADDR, so that is how a real running
        PalmSentinel holds a port.  A probe that set the same flag would consider
        such a port free -- which is the whole defect -- so the probe must detect
        it anyway, and this test is what says so.  `reuse=False` covers the
        ordinary foreign listener.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        if reuse:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)

    def test_the_first_instance_gets_the_base_port(self) -> None:
        claimed = self._claim(base=self.base)
        self.assertEqual(claimed.port, self.base)
        self.assertFalse(claimed.shifted)
        self.assertEqual(claimed.url, f"http://127.0.0.1:{self.base}")

    def test_a_second_instance_never_gets_the_same_port(self) -> None:
        first = self._claim(base=self.base)
        second = self._claim(base=self.base)
        self.assertEqual(first.port, self.base)
        self.assertNotEqual(second.port, first.port)
        self.assertTrue(second.shifted)
        message = second.announce()
        self.assertIn(str(second.port), message)
        self.assertIn(str(self.base), message)

    def test_an_explicit_port_is_honoured_or_refused_never_moved(self) -> None:
        first = self._claim(self.base)
        self.assertEqual(first.port, self.base)
        self.assertFalse(first.shifted)
        with self.assertRaises(ports.PortUnavailable) as caught:
            self._claim(self.base)
        message = str(caught.exception)
        self.assertEqual(caught.exception.port, self.base)
        self.assertIn(str(self.base), message)
        self.assertIn(f"--port {self.base + 1}", message)

    def test_a_port_held_the_way_a_real_instance_holds_it_is_passed_over(self) -> None:
        self._squatter(self.base, reuse=True)
        claimed = self._claim(base=self.base)
        self.assertNotEqual(claimed.port, self.base)
        self.assertTrue(claimed.shifted)

    def test_a_port_held_by_a_plain_listener_is_passed_over(self) -> None:
        self._squatter(self.base, reuse=False)
        claimed = self._claim(base=self.base)
        self.assertNotEqual(claimed.port, self.base)
        self.assertTrue(claimed.shifted)

    def test_an_explicit_port_held_by_something_else_is_refused(self) -> None:
        self._squatter(self.base, reuse=True)
        with self.assertRaises(ports.PortUnavailable):
            self._claim(self.base)

    def test_two_instances_starting_at_the_same_instant_get_different_ports(self) -> None:
        # The double-click case: released together, so neither can rely on the
        # other having bound already.  The lock file is the arbiter.
        results: list = []
        gate = threading.Barrier(2)

        def run() -> None:
            gate.wait()
            try:
                results.append(ports.claim(base=self.base))
            except ports.PortUnavailable:
                results.append(None)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        claimed = [result for result in results if result is not None]
        self.claims.extend(claimed)
        ports_found = [result.port for result in claimed]
        self.assertEqual(len(ports_found), 2, f"both must claim a port, got {results}")
        self.assertEqual(
            len(set(ports_found)), 2, f"they must not share one, got {ports_found}"
        )

    def test_releasing_frees_the_port_for_the_next_claim(self) -> None:
        first = self._claim(base=self.base)
        ports.release(first)
        self.claims.remove(first)
        again = self._claim(base=self.base)
        self.assertEqual(again.port, self.base)

    def test_a_crashed_instances_lock_does_not_block_the_port_forever(self) -> None:
        # A lock file with nobody holding it open is what a hard kill leaves
        # behind.  It must not be able to take a port out of service.
        first = self._claim(base=self.base)
        path = ports._lock_path(first.port)
        ports.release(first)
        self.claims.remove(first)
        with open(path, "w", encoding="ascii") as handle:
            handle.write("999999\n")
        again = self._claim(base=self.base)
        self.assertEqual(again.port, self.base)

    def test_a_fully_busy_window_says_so_instead_of_walking_off(self) -> None:
        base = _free_run(3)
        for offset in range(3):
            self._squatter(base + offset)
        with self.assertRaises(ports.PortUnavailable) as caught:
            self._claim(base=base, window=3)
        self.assertIn(str(base), str(caught.exception))
        self.assertIn(str(base + 2), str(caught.exception))


class TestAnnounce(unittest.TestCase):
    def test_the_announcement_names_both_ports_when_shifted(self) -> None:
        claimed = ports.Claim(port=5001, base=5000)
        self.assertTrue(claimed.shifted)
        self.assertEqual(
            claimed.announce(),
            "[PalmSentinel] port 5000 is already in use; serving on http://127.0.0.1:5001 instead",
        )

    def test_the_announcement_is_a_single_line_when_not_shifted(self) -> None:
        claimed = ports.Claim(port=5000, base=5000)
        self.assertFalse(claimed.shifted)
        self.assertEqual(claimed.announce(), "[PalmSentinel] serving on http://127.0.0.1:5000")


if __name__ == "__main__":
    unittest.main()
