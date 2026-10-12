"""Tests for slow, hung and unreachable backends.

The load balancer uses a short CONNECT timeout (so an unreachable backend
fails over quickly) and a longer READ timeout (so a slow but healthy backend
is allowed to finish). Retries only happen when that is safe, and the total
time for one client request is bounded by REQUEST_DEADLINE.

Fake backends hold /slow responses until the test releases them
(FakeBackend.gate), so "a slow request is in flight" is controlled by the
test. The timeouts are patched to a fraction of a second so the timeout
paths run quickly.
"""

import http.client
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import LoadBalancerTestCase, QuietHandler
import balancer
from circuit_breaker import CLOSED, OPEN

BACKEND_APP = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "backend", "app.py"
)


class SlowRequestTests(LoadBalancerTestCase):

    def test_slow_response_succeeds_without_retry_or_failure(self):
        self._patch(balancer, "READ_TIMEOUT", 5.0)

        request = self.get_async("/slow")

        self.assertTrue(self.fakes[0].slow_started.wait(5))
        self.assertFalse(request.done())           # still in flight

        self.fakes[0].gate.set()                   # backend finishes

        status, served_by, _ = request.wait()

        self.assertEqual(status, 200)
        self.assertEqual(served_by, self.name(0))

        # One attempt only: nothing was retried or counted as a failure.
        self.assertEqual(self.fakes[1].hits, 0)
        self.assertEqual(self.fakes[2].hits, 0)
        self.assertEqual(self.backends[0].errors, 0)
        self.assertEqual(self.backends[0].total_requests, 1)
        self.assertEqual(self.state(0), CLOSED)
        self.assertEqual(
            self.backends[0].breaker.snapshot()["consecutive_failures"], 0
        )

        self.assert_no_leaked_connections()

    def test_waiting_for_a_response_is_not_limited_by_connect_timeout(self):
        # A very short connect timeout must not cut off a slow response.
        self._patch(balancer, "CONNECT_TIMEOUT", 0.05)
        self._patch(balancer, "READ_TIMEOUT", 5.0)

        request = self.get_async("/slow")

        self.assertTrue(self.fakes[0].slow_started.wait(5))
        time.sleep(0.3)                            # many connect-timeouts long

        self.assertFalse(request.done())

        self.fakes[0].gate.set()

        status, served_by, _ = request.wait()

        self.assertEqual((status, served_by), (200, self.name(0)))
        self.assertEqual(self.backends[0].errors, 0)

        self.assert_no_leaked_connections()

    def test_unresponsive_backend_times_out_and_request_fails_over(self):
        self._patch(balancer, "READ_TIMEOUT", 0.3)

        # Backends 1 and 2 answer immediately; backend 0 accepts the
        # request but never answers.
        self.fakes[1].gate.set()
        self.fakes[2].gate.set()

        status, served_by, _ = self.get("/slow")

        self.assertEqual(status, 200)
        self.assertNotEqual(served_by, self.name(0))

        # Backend 0 did receive the request, timed out, and was charged
        # with one failure. Exactly one other backend answered.
        self.assertEqual(self.fakes[0].hits_for("/slow"), 1)
        self.assertEqual(self.backends[0].errors, 1)
        self.assertEqual(
            self.backends[0].breaker.snapshot()["consecutive_failures"], 1
        )
        self.assertEqual(self.fakes[1].hits + self.fakes[2].hits, 1)

        self.assert_no_leaked_connections()

    def test_hung_backend_is_removed_from_rotation_by_circuit_breaker(self):
        self._patch(balancer, "READ_TIMEOUT", 0.2)

        # Backends 1 and 2 answer /slow immediately; backend 0 hangs.
        self.fakes[1].gate.set()
        self.fakes[2].gate.set()

        results = []

        for _ in range(30):
            if self.state(0) == OPEN:
                break

            results.append(self.get("/slow"))

        self.assertEqual(self.state(0), OPEN)

        # Clients never saw the hung backend.
        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertNotIn(self.name(0), {name for _, name, _ in results})

        # Once the circuit is open, backend 0 is no longer contacted.
        contacted = self.fakes[0].hits_for("/slow")
        self.assertEqual(contacted, self.backends[0].breaker.failure_threshold)

        later = [self.get("/slow") for _ in range(12)]

        self.assertEqual({status for status, _, _ in later}, {200})
        self.assertEqual(self.fakes[0].hits_for("/slow"), contacted)

        self.assert_no_leaked_connections()

    def test_timeout_on_every_backend_returns_504(self):
        self._patch(balancer, "READ_TIMEOUT", 0.2)

        started = time.monotonic()
        status, served_by, body = self.get("/slow")
        elapsed = time.monotonic() - started

        self.assertEqual(status, 504)
        self.assertIsNone(served_by)
        self.assertEqual(
            json.loads(body), {"error": "Backend response timed out"}
        )

        # Every backend was tried once and charged one failure.
        for index in range(3):
            self.assertEqual(self.fakes[index].hits_for("/slow"), 1)
            self.assertEqual(self.backends[index].errors, 1)

        self.assertLess(elapsed, 5.0)

        self.assert_no_leaked_connections()

    def test_request_deadline_bounds_total_time_and_stops_retrying(self):
        self._patch(balancer, "READ_TIMEOUT", 5.0)
        self._patch(balancer, "REQUEST_DEADLINE", 0.3)

        started = time.monotonic()
        status, _, body = self.get("/slow")
        elapsed = time.monotonic() - started

        # The first attempt used up the whole budget (0.3 s, not 5 s),
        # so no second backend was tried.
        self.assertEqual(status, 504)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(self.fakes[0].hits_for("/slow"), 1)
        self.assertEqual(self.fakes[1].hits, 0)
        self.assertEqual(self.fakes[2].hits, 0)

        self.assert_no_leaked_connections()

    def test_unreachable_backend_fails_over_without_waiting_for_read_timeout(self):
        self._patch(balancer, "READ_TIMEOUT", 30.0)

        self.fakes[0].stop()                       # connection refused

        started = time.monotonic()
        status, served_by, _ = self.get("/")
        elapsed = time.monotonic() - started

        self.assertEqual(status, 200)
        self.assertNotEqual(served_by, self.name(0))
        self.assertLess(elapsed, 5.0)              # nowhere near 30 s
        self.assertEqual(self.backends[0].errors, 1)

        self.assert_no_leaked_connections()

    # ------------------------------------------------------------------
    # Retry policy for methods that are not safe to repeat
    # ------------------------------------------------------------------

    def test_unsafe_method_is_not_repeated_after_request_was_sent(self):
        self._patch(balancer, "READ_TIMEOUT", 0.2)

        status, _, body = self.get("/slow", method="DELETE")

        # Backend 0 received the request and then stopped answering. It may
        # have processed it, so it must NOT be repeated on another backend.
        self.assertEqual(status, 502)
        self.assertIn("not repeated", json.loads(body)["error"])
        self.assertEqual(self.fakes[0].hits_for("/slow"), 1)
        self.assertEqual(self.fakes[1].hits, 0)
        self.assertEqual(self.fakes[2].hits, 0)

        self.assert_no_leaked_connections()

    def test_unsafe_method_is_retried_when_connection_was_never_made(self):
        self.fakes[0].stop()                       # refused: nothing was sent

        status, served_by, _ = self.get("/", method="DELETE")

        self.assertEqual(status, 200)
        self.assertNotEqual(served_by, self.name(0))
        self.assertEqual(self.fakes[1].hits + self.fakes[2].hits, 1)

        self.assert_no_leaked_connections()

    def test_retry_policy(self):
        policy = balancer.is_retry_safe

        # Never sent: always safe to try another backend.
        for method in ("GET", "POST", "PUT", "DELETE"):
            with self.subTest(method=method, request_sent=False):
                self.assertTrue(policy(method, False))

        # Sent: only methods that are safe to repeat.
        for method in ("GET", "HEAD", "OPTIONS", "get"):
            with self.subTest(method=method, request_sent=True):
                self.assertTrue(policy(method, True))

        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method, request_sent=True):
                self.assertFalse(policy(method, True))


class ClientDisconnectingHandler(QuietHandler):
    """A load balancer whose client goes away while the response is written."""

    def end_headers(self):
        raise BrokenPipeError("client went away")


class ClientDisconnectTests(LoadBalancerTestCase):

    handler_class = ClientDisconnectingHandler

    def test_failure_writing_to_the_client_is_not_retried_or_blamed_on_the_backend(self):
        # The backend answered correctly. The client then disappeared while
        # the load balancer was writing the response. That must not be
        # treated as a backend failure, and the request must not be sent to
        # a second backend.
        with self.assertRaises((http.client.RemoteDisconnected, ConnectionError)):
            self.get("/")

        self.wait_until(
            lambda: self.backends[0].total_requests == 1,
            message="the first backend's answer to be recorded"
        )

        self.assertEqual(self.fakes[0].hits, 1)
        self.assertEqual(self.fakes[1].hits, 0)
        self.assertEqual(self.fakes[2].hits, 0)

        self.assertEqual(self.backends[0].errors, 0)
        self.assertEqual(self.state(0), CLOSED)
        self.assertEqual(
            self.backends[0].breaker.snapshot()["consecutive_failures"], 0
        )

        self.assert_no_leaked_connections()


class TimeoutConfigurationTests(unittest.TestCase):

    def test_read_timeout_default_allows_the_backends_slow_endpoint(self):
        # Regression: /slow takes 5 s by default, but the load balancer used
        # to give up after 3 s, so every /slow request failed over and
        # ended as a 503. Read the backend's default straight from its
        # source so the two settings cannot drift apart again.
        with open(BACKEND_APP) as source:
            match = re.search(r"^DEFAULT_SLOW_MS\s*=\s*(\d+)", source.read(), re.M)

        self.assertIsNotNone(match, "DEFAULT_SLOW_MS not found in backend/app.py")

        slow_seconds = int(match.group(1)) / 1000

        self.assertGreater(balancer.DEFAULT_READ_TIMEOUT, slow_seconds)
        self.assertGreaterEqual(
            balancer.DEFAULT_REQUEST_DEADLINE, balancer.DEFAULT_READ_TIMEOUT
        )

    def test_connect_timeout_is_much_shorter_than_read_timeout(self):
        # An unreachable backend must fail over quickly.
        self.assertLess(
            balancer.DEFAULT_CONNECT_TIMEOUT * 3,
            balancer.DEFAULT_READ_TIMEOUT
        )

    def test_read_seconds_uses_default_when_unset_or_blank(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VANTORA_TEST_SECONDS", None)
            self.assertEqual(balancer.read_seconds("VANTORA_TEST_SECONDS", 7.5), 7.5)

        with mock.patch.dict(os.environ, {"VANTORA_TEST_SECONDS": "  "}):
            self.assertEqual(balancer.read_seconds("VANTORA_TEST_SECONDS", 7.5), 7.5)

    def test_read_seconds_reads_a_valid_value(self):
        with mock.patch.dict(os.environ, {"VANTORA_TEST_SECONDS": "2.5"}):
            self.assertEqual(balancer.read_seconds("VANTORA_TEST_SECONDS", 7.5), 2.5)

    def test_read_seconds_rejects_invalid_values(self):
        for bad in ("abc", "0", "-3"):
            with self.subTest(value=bad):
                with mock.patch.dict(os.environ, {"VANTORA_TEST_SECONDS": bad}):
                    with self.assertRaises(ValueError):
                        balancer.read_seconds("VANTORA_TEST_SECONDS", 7.5)


if __name__ == "__main__":
    unittest.main()
