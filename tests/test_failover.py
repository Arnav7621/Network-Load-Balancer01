"""Integration tests for Vantora's failover and circuit-breaker behaviour.

These tests run the REAL load-balancer request handler (balancer.py) in front
of small fake backend servers that listen on free ports chosen by the
operating system. Real HTTP requests go through real sockets, so a test fails
if the load balancer's retry, health-exclusion or circuit-breaker logic breaks.

Isolation:
  * Every server uses port 0 (a free port), so nothing clashes with 9001-9003
    or with a running Vantora instance.
  * The module-level `backends` / `algorithm` of balancer.py are swapped for
    the duration of each test and restored afterwards.
  * All servers are stopped with addCleanup(), which runs even if an
    assertion fails.
  * The background health-check thread is NOT started. Health-check results
    are fed in directly with backend.record_health_failure(), so the circuit
    breaker and the health monitor stay clearly separate.

Not covered here: a backend that hangs (the load balancer waits 3 seconds per
attempt, which would slow the suite) and the Docker network.
"""

import http.client
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

# Allow Python to import files from the lb folder
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "lb")
    )
)

import algorithms
import balancer
from algorithms import Backend, RoundRobin
from circuit_breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker

LOCALHOST = "127.0.0.1"


class FakeBackend:
    """A tiny real HTTP server that stands in for one Vantora backend."""

    def __init__(self):
        self.status = 200   # status returned for every path except /health
        self.hits = 0       # requests received, not counting /health
        self.port = None
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self, port=0):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    status = 200
                else:
                    status = owner.status
                    with owner._lock:
                        owner.hits += 1

                body = json.dumps({"status": status}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer((LOCALHOST, port), Handler)
        self.port = self._server.server_address[1]

        server = self._server
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01),
            daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop listening, so new connections are refused. Safe to call twice."""
        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._server = None
        self._thread = None


class QuietHandler(balancer.LoadBalancerHandler):
    """The real handler, with per-request logging switched off."""

    def log_message(self, *args):
        pass


class FailoverIntegrationTests(unittest.TestCase):

    def setUp(self):
        # Three fake backends on free ports.
        self.fakes = [FakeBackend() for _ in range(3)]

        for fake in self.fakes:
            fake.start()
            self.addCleanup(fake.stop)

        # Real Backend objects (real circuit breakers) pointing at them.
        self.backends = [
            Backend(LOCALHOST, fake.port) for fake in self.fakes
        ]

        # Point the real load-balancer code at these backends, using
        # Round Robin so the order of selection is predictable.
        self._patch(balancer, "backends", self.backends)
        self._patch(balancer, "algorithm", RoundRobin())
        self._patch(balancer, "algorithm_name", "Round Robin")

        # Keep the test output readable: the load balancer prints a line for
        # every failure and circuit change.
        self._patch(balancer, "print", mock.MagicMock(), create=True)
        self._patch(algorithms, "print", mock.MagicMock(), create=True)

        # The real load balancer, listening on a free port.
        self.lb_server = ThreadingHTTPServer(
            (LOCALHOST, 0),
            QuietHandler
        )
        self.lb_port = self.lb_server.server_address[1]

        lb_thread = threading.Thread(
            target=lambda: self.lb_server.serve_forever(poll_interval=0.01),
            daemon=True
        )
        lb_thread.start()

        def stop_lb():
            self.lb_server.shutdown()
            self.lb_server.server_close()
            lb_thread.join(timeout=5)

        self.addCleanup(stop_lb)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _patch(self, target, attribute, value, **kwargs):
        patcher = mock.patch.object(target, attribute, value, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def name(self, index):
        """The X-Served-By value for backend `index`."""
        return f"Backend-{self.backends[index].port}"

    def get(self, path="/"):
        """Send one request through the load balancer: (status, served_by, body)."""
        connection = http.client.HTTPConnection(
            LOCALHOST, self.lb_port, timeout=10
        )
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            return (
                response.status,
                response.getheader("X-Served-By"),
                body
            )
        finally:
            connection.close()

    def get_many(self, count):
        return [self.get() for _ in range(count)]

    def state(self, index):
        return self.backends[index].breaker.snapshot()["state"]

    def use_fast_breaker(self, index, open_seconds=0.3):
        """Give a backend the real CircuitBreaker with a short cooldown.

        Returns the list in which every state change is recorded.
        """
        transitions = []

        self.backends[index].breaker = CircuitBreaker(
            name=f"test-{index}",
            failure_threshold=3,
            open_seconds=open_seconds,
            half_open_max_probes=1,
            success_threshold=1,
            on_state_change=lambda name, old, new: transitions.append(
                (old, new)
            )
        )

        return transitions

    def drive_until_open(self, index, max_requests=40):
        """Send traffic until the circuit of backend `index` opens."""
        for _ in range(max_requests):
            if self.state(index) == OPEN:
                return

            self.get()

        self.fail(f"Circuit of backend {index} did not open")

    def assert_no_leaked_connections(self, timeout=2.0):
        # The load balancer lowers active_conns just AFTER it has sent the
        # response, so the last handler thread may still be finishing when
        # the client returns. Wait for it; a genuine leak never reaches 0.
        deadline = time.time() + timeout

        while time.time() < deadline:
            if all(b.active_conns == 0 for b in self.backends):
                return

            time.sleep(0.01)

        for backend in self.backends:
            self.assertEqual(
                backend.active_conns,
                0,
                f"Backend-{backend.port} leaked an active connection"
            )

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_requests_succeed_and_failed_backend_is_excluded(self):
        self.fakes[1].stop()                       # backend 1 becomes unavailable

        results = self.get_many(30)
        statuses = {status for status, _, _ in results}
        served_by = {name for _, name, _ in results}

        # Clients never see the failure.
        self.assertEqual(statuses, {200})

        # The unavailable backend never served a request...
        self.assertNotIn(self.name(1), served_by)

        # ...and the two healthy backends shared the traffic.
        self.assertEqual(served_by, {self.name(0), self.name(2)})

        # The failures were counted against the dead backend only, and its
        # circuit opened after exactly the configured number of failures.
        dead = self.backends[1]
        self.assertEqual(self.state(1), OPEN)
        self.assertEqual(dead.errors, dead.breaker.failure_threshold)
        self.assertEqual(self.backends[0].errors, 0)
        self.assertEqual(self.backends[2].errors, 0)
        self.assertEqual(self.state(0), CLOSED)
        self.assertEqual(self.state(2), CLOSED)

        self.assert_no_leaked_connections()

    def test_open_circuit_stops_further_attempts_on_failed_backend(self):
        self.fakes[1].stop()
        self.drive_until_open(1)

        dead = self.backends[1]
        errors_when_opened = dead.errors

        results = self.get_many(20)

        self.assertEqual({status for status, _, _ in results}, {200})

        # No new attempts reached the dead backend while its circuit was OPEN.
        self.assertEqual(dead.errors, errors_when_opened)
        self.assertEqual(dead.breaker.snapshot()["times_opened"], 1)
        self.assertEqual(self.state(1), OPEN)

        self.assert_no_leaked_connections()

    def test_circuit_closes_after_backend_recovers(self):
        transitions = self.use_fast_breaker(1, open_seconds=0.3)
        dead_port = self.backends[1].port

        self.fakes[1].stop()
        self.drive_until_open(1)

        # Bring the backend back on the same port and let the cooldown pass.
        self.fakes[1].start(port=dead_port)
        time.sleep(0.5)

        # The next request routed to it is the HALF_OPEN trial request.
        results = self.get_many(9)

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertIn(self.name(1), {name for _, name, _ in results})
        self.assertEqual(self.state(1), CLOSED)
        self.assertEqual(
            transitions,
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, CLOSED)]
        )

        self.assert_no_leaked_connections()

    def test_failed_trial_request_reopens_circuit_without_failing_client(self):
        transitions = self.use_fast_breaker(1, open_seconds=0.3)

        self.fakes[1].stop()                       # stays down
        self.drive_until_open(1)
        time.sleep(0.5)                            # cooldown passes

        results = self.get_many(9)

        # The trial request failed, but the client was still served by a
        # healthy backend.
        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertNotIn(self.name(1), {name for _, name, _ in results})

        # The breaker went OPEN -> HALF_OPEN (trial) -> OPEN (trial failed).
        self.assertEqual(
            transitions[:3],
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, OPEN)]
        )

        self.assert_no_leaked_connections()

    def test_backend_marked_down_by_health_check_receives_no_traffic(self):
        # Three consecutive failed health checks mark backend 0 DOWN.
        for _ in range(3):
            self.backends[0].record_health_failure()

        self.assertFalse(self.backends[0].healthy)

        results = self.get_many(30)

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertEqual(self.fakes[0].hits, 0)
        self.assertGreater(self.fakes[1].hits, 0)
        self.assertGreater(self.fakes[2].hits, 0)

        # The backend was excluded because of its health status, not because
        # of failed requests, so its circuit breaker is untouched.
        self.assertEqual(self.state(0), CLOSED)
        self.assertEqual(self.backends[0].errors, 0)

        self.assert_no_leaked_connections()

    def test_all_backends_down_returns_503(self):
        for fake in self.fakes:
            fake.stop()

        results = self.get_many(5)

        self.assertEqual({status for status, _, _ in results}, {503})

        status, served_by, body = results[0]
        self.assertIsNone(served_by)
        self.assertEqual(
            json.loads(body),
            {"error": "All backend servers are unavailable"}
        )

        # Repeated failures opened every circuit.
        for index in range(3):
            self.assertEqual(self.state(index), OPEN)

        self.assert_no_leaked_connections()

    def test_server_error_is_forwarded_and_counts_toward_circuit(self):
        # Current behaviour: a 5xx response is a failure for the circuit
        # breaker, but it is forwarded to the client rather than retried.
        self.fakes[1].status = 500

        results = self.get_many(30)
        errors_seen = [
            (status, name)
            for status, name, _ in results
            if status == 500
        ]

        threshold = self.backends[1].breaker.failure_threshold

        self.assertEqual(len(errors_seen), threshold)
        self.assertTrue(all(name == self.name(1) for _, name in errors_seen))
        self.assertEqual(self.state(1), OPEN)
        self.assertEqual(self.backends[1].errors, threshold)

        # Once the circuit is open, every remaining request succeeds.
        self.assertEqual(
            sum(1 for status, _, _ in results if status == 200),
            30 - threshold
        )

        self.assert_no_leaked_connections()


if __name__ == "__main__":
    unittest.main()
