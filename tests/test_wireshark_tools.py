"""Tests for the evidence tools in docs/wireshark/.

* generate_traffic.py is run against the REAL load-balancer handler and its
  per-backend counts are checked against the documented expectations.
* analyze_capture.py is run on a small SYNTHETIC capture built inside the
  test. That file is a test fixture only; it is not evidence and is never
  written to the repository.

The analyzer tests are skipped if scapy is not installed.
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import LoadBalancerTestCase

WIRESHARK_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "wireshark"
)
GENERATOR = os.path.join(WIRESHARK_DIR, "generate_traffic.py")
ANALYZER = os.path.join(WIRESHARK_DIR, "analyze_capture.py")

HAS_SCAPY = importlib.util.find_spec("scapy") is not None


class TrafficGeneratorTests(LoadBalancerTestCase):

    def run_generator(self, *arguments):
        result = subprocess.run(
            [sys.executable, GENERATOR, "--lb", f"http://127.0.0.1:{self.lb_port}",
             *arguments],
            capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def answered_by(self, output):
        """{'Backend-<port>': count} from the generator's summary table."""
        return {
            name: int(count)
            for name, count in re.findall(r"^\s+(Backend-\d+)\s+(\d+)\s", output, re.M)
        }

    def test_round_robin_recipe_gives_an_even_split(self):
        output = self.run_generator("--algorithm", "Round Robin", "--requests", "30")

        self.assertEqual(
            self.answered_by(output),
            {self.name(0): 10, self.name(1): 10, self.name(2): 10}
        )
        self.assertIn("200 x30", output)

    def test_weighted_recipe_follows_the_weights(self):
        for backend, weight in zip(self.backends, (3, 2, 1)):
            backend.weight = weight

        output = self.run_generator(
            "--algorithm", "Weighted Round Robin", "--requests", "30"
        )

        self.assertEqual(
            self.answered_by(output),
            {self.name(0): 15, self.name(1): 10, self.name(2): 5}
        )

    def test_least_connections_recipe_needs_overlapping_requests(self):
        # Sequential requests never overlap, so everything goes to the first
        # backend. This is why the Wireshark recipe uses --concurrency.
        output = self.run_generator(
            "--algorithm", "Least Connections", "--requests", "12"
        )

        self.assertEqual(self.answered_by(output), {self.name(0): 12})

    def test_unknown_algorithm_is_rejected_before_sending_traffic(self):
        result = subprocess.run(
            [sys.executable, GENERATOR, "--lb", f"http://127.0.0.1:{self.lb_port}",
             "--algorithm", "Fastest", "--requests", "3"],
            capture_output=True, text=True, timeout=60
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.fakes[0].hits + self.fakes[1].hits + self.fakes[2].hits, 0)


@unittest.skipUnless(HAS_SCAPY, "scapy is not installed")
class CaptureAnalyzerTests(unittest.TestCase):

    def build_capture(self, path):
        from scapy.all import IP, TCP, Raw, wrpcap

        def tcp(sport, dport, payload=None, flags="PA"):
            packet = IP(src="127.0.0.1", dst="127.0.0.1") / TCP(
                sport=sport, dport=dport, flags=flags
            )
            return packet / Raw(payload) if payload else packet

        def get(uri):
            return f"GET {uri} HTTP/1.1\r\nHost: x\r\n\r\n".encode()

        def response(backend):
            return (
                f"HTTP/1.0 200 OK\r\nX-Served-By: {backend}\r\n\r\n{{}}"
            ).encode()

        packets = []
        packets += [tcp(50000 + i, 8080, get("/")) for i in range(3)]       # 3 client GETs
        packets += [tcp(51000, 8080, b"POST /algorithm HTTP/1.1\r\n\r\n")]    # not a GET
        packets += [tcp(52000, 9001, get("/health")) for _ in range(2)]      # health probes
        packets += [tcp(52001, 9001, get("/"))]                              # 1 proxied to 9001
        packets += [tcp(52002, 9002, get("/")) for _ in range(2)]            # 2 proxied to 9002
        packets += [tcp(8080, 50000, response("Backend-9001"))]
        packets += [tcp(8080, 50001, response("Backend-9002")) for _ in range(2)]
        packets += [tcp(9003, 52003, flags="RA") for _ in range(2)]          # 9003 refuses twice

        wrpcap(path, packets)

    def analyze(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "synthetic.pcap")
            self.build_capture(path)

            result = subprocess.run(
                [sys.executable, ANALYZER, path],
                capture_output=True, text=True, timeout=60
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def row(self, output, port):
        match = re.search(rf"^{port}\s+\S+(?: \S+)?\s+(\d+)\s+(\d+)\s+(\d+)\s*$", output, re.M)
        self.assertIsNotNone(match, f"no table row for port {port}:\n{output}")
        return tuple(int(value) for value in match.groups())    # GETs, probes, RST

    def test_counts_client_requests_health_probes_and_refusals_separately(self):
        output = self.analyze()

        self.assertEqual(self.row(output, 8080), (3, 0, 0))
        self.assertEqual(self.row(output, 9001), (1, 2, 0))
        self.assertEqual(self.row(output, 9002), (2, 0, 0))
        self.assertEqual(self.row(output, 9003), (0, 0, 2))

    def test_reports_who_answered_clients_and_how_many_backends_were_used(self):
        output = self.analyze()

        self.assertRegex(output, r"Backend-9001\s+1\s+\(33\.3%\)")
        self.assertRegex(output, r"Backend-9002\s+2\s+\(66\.7%\)")
        self.assertIn("Backends that received client requests: 2 of 3", output)

    def test_other_methods_are_listed_instead_of_counted_as_client_requests(self):
        self.assertIn("POST /algorithm x1", self.analyze())


if __name__ == "__main__":
    unittest.main()
