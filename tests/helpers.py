"""Shared fixtures for Vantora's integration tests.

The integration tests run the REAL load-balancer request handler
(lb/balancer.py) in front of small fake backend servers. Everything listens
on free ports chosen by the operating system, so nothing clashes with
9001-9003 or with a running Vantora instance.

Determinism:
  * FakeClock replaces the clock of a circuit breaker, so cooldowns are
    advanced by the test instead of waiting for real time to pass.
  * FakeBackend.gate holds /slow responses until the test releases them, so
    "a slow request is in flight" is a state the test controls, not a
    timing guess.
  * The background health-check thread is never started. Health results are
    fed in directly with backend.record_health_failure().
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
from circuit_breaker import CircuitBreaker

LOCALHOST = "127.0.0.1"


class FakeClock:
    """A clock that only moves when the test advances it."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeBackend:
    """A tiny real HTTP server that stands in for one Vantora backend.

    * /health always answers 200 and is not counted.
    * Paths starting with /slow wait until `gate` is set (or `hold_seconds`
      pass, a safety limit so a broken test can never hang forever).
      `slow_started` is set as soon as a slow request has reached the
      backend.
    * Every other path answers immediately with `status`.
    """

    def __init__(self):
        self.status = 200
        self.paths = []                       # non-health paths, in order
        self.gate = threading.Event()
        self.slow_started = threading.Event()
        self.hold_seconds = 10.0
        self.port = None
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    @property
    def hits(self):
        with self._lock:
            return len(self.paths)

    def hits_for(self, path):
        with self._lock:
            return self.paths.count(path)

    def start(self, port=0):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    status = 200
                else:
                    with owner._lock:
                        owner.paths.append(self.path)

                    if self.path.startswith("/slow"):
                        owner.slow_started.set()
                        owner.gate.wait(owner.hold_seconds)

                    status = owner.status

                body = json.dumps({"status": status}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()

                try:
                    self.wfile.write(body)
                except OSError:
                    pass                      # the load balancer gave up

            do_DELETE = do_GET

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
        self.gate.set()                       # release any held handler threads

        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._server = None
        self._thread = None


class QuietHandler(balancer.LoadBalancerHandler):
    """The real handler, with logging off and DELETE routed through the proxy.

    The real load balancer proxies GET only. DELETE is wired up here, in the
    test, so the retry policy for methods that are not safe to repeat can be
    exercised through the real proxy code.
    """

    def do_DELETE(self):
        self.proxy_request("DELETE")

    def log_message(self, *args):
        pass


class AsyncRequest:
    """One request sent through the load balancer from a background thread."""

    def __init__(self, case, path, method="GET"):
        self.result = None
        self.error = None
        self._thread = threading.Thread(
            target=self._run,
            args=(case, path, method),
            daemon=True
        )
        self._thread.start()

    def _run(self, case, path, method):
        try:
            self.result = case.get(path, method=method, timeout=15)
        except Exception as error:            # surfaced by wait()
            self.error = error

    def done(self):
        return not self._thread.is_alive()

    def wait(self, timeout=15):
        self._thread.join(timeout)

        if self._thread.is_alive():
            raise AssertionError("request did not finish in time")

        if self.error is not None:
            raise self.error

        return self.result


class LoadBalancerTestCase(unittest.TestCase):
    """Runs the real load balancer in front of three fake backends."""

    # A subclass can serve a different handler (for example one that fails
    # while writing to the client).
    handler_class = QuietHandler

    def setUp(self):
        self.clock = FakeClock()

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

        # Keep the test output readable.
        self._patch(balancer, "print", mock.MagicMock(), create=True)
        self._patch(algorithms, "print", mock.MagicMock(), create=True)

        # The real load balancer, listening on a free port.
        self.lb_server = ThreadingHTTPServer(
            (LOCALHOST, 0),
            self.handler_class
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

    def use_algorithm(self, algorithm):
        """Switch the load balancer to `algorithm` (restored after the test)."""
        balancer.algorithm = algorithm

    def name(self, index):
        """The X-Served-By value for backend `index`."""
        return f"Backend-{self.backends[index].port}"

    def get(self, path="/", method="GET", timeout=10):
        """Send one request through the load balancer: (status, served_by, body)."""
        connection = http.client.HTTPConnection(
            LOCALHOST, self.lb_port, timeout=timeout
        )
        try:
            connection.request(method, path)
            response = connection.getresponse()
            body = response.read()
            return (
                response.status,
                response.getheader("X-Served-By"),
                body
            )
        finally:
            connection.close()

    def get_many(self, count, path="/"):
        return [self.get(path) for _ in range(count)]

    def get_async(self, path="/", method="GET"):
        return AsyncRequest(self, path, method)

    def state(self, index):
        return self.backends[index].breaker.snapshot()["state"]

    def use_controlled_breaker(self, index, open_seconds=10):
        """Give a backend the real CircuitBreaker, driven by self.clock.

        The cooldown only passes when the test calls self.clock.advance().
        Returns the list in which every state change is recorded.
        """
        transitions = []

        self.backends[index].breaker = CircuitBreaker(
            name=f"test-{index}",
            failure_threshold=3,
            open_seconds=open_seconds,
            half_open_max_probes=1,
            success_threshold=1,
            clock=self.clock,
            on_state_change=lambda name, old, new: transitions.append(
                (old, new)
            )
        )

        return transitions

    def drive_until_open(self, index, max_requests=40):
        """Send traffic until the circuit of backend `index` opens."""
        for _ in range(max_requests):
            if self.state(index) == "OPEN":
                return

            self.get()

        self.fail(f"Circuit of backend {index} did not open")

    def wait_until(self, condition, timeout=5.0, message="condition"):
        """Poll until `condition()` is true; fail if it never becomes true."""
        deadline = time.time() + timeout

        while time.time() < deadline:
            if condition():
                return

            time.sleep(0.005)

        self.fail(f"Timed out waiting for {message}")

    def assert_no_leaked_connections(self, timeout=2.0):
        self.wait_until(
            lambda: all(b.active_conns == 0 for b in self.backends),
            timeout=timeout,
            message="active connections to return to 0"
        )
