"""Integration tests for Vantora's failover and circuit-breaker behaviour.

These tests run the REAL load-balancer request handler (lb/balancer.py) in
front of small fake backend servers on free ports (see helpers.py), so real
HTTP requests go through real sockets and a test fails if the load
balancer's retry, health-exclusion or circuit-breaker logic breaks.

Circuit-breaker cooldowns are driven by a fake clock, so no test waits for
real time to pass. The background health-check thread is not started;
health-check results are fed in directly.

Slow and hung backends are covered in test_slow_requests.py, and mixed
workloads in test_mixed_workload.py.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import LoadBalancerTestCase
from circuit_breaker import CLOSED, HALF_OPEN, OPEN


class FailoverIntegrationTests(LoadBalancerTestCase):

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
        transitions = self.use_controlled_breaker(1, open_seconds=10)
        dead_port = self.backends[1].port

        self.fakes[1].stop()
        self.drive_until_open(1)

        # Bring the backend back on the same port and let the cooldown pass.
        self.fakes[1].start(port=dead_port)
        self.clock.advance(10)

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

    def test_circuit_stays_open_until_cooldown_has_passed(self):
        transitions = self.use_controlled_breaker(1, open_seconds=10)
        dead_port = self.backends[1].port

        self.fakes[1].stop()
        self.drive_until_open(1)
        self.fakes[1].start(port=dead_port)        # healthy again...

        self.clock.advance(9.9)                    # ...but cooldown not over

        results = self.get_many(12)

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertNotIn(self.name(1), {name for _, name, _ in results})
        self.assertEqual(self.state(1), OPEN)
        self.assertEqual(transitions, [(CLOSED, OPEN)])

        self.assert_no_leaked_connections()

    def test_failed_trial_request_reopens_circuit_without_failing_client(self):
        transitions = self.use_controlled_breaker(1, open_seconds=10)

        self.fakes[1].stop()                       # stays down
        self.drive_until_open(1)
        self.clock.advance(10)                     # cooldown passes

        results = self.get_many(9)

        # The trial request failed, but the client was still served by a
        # healthy backend.
        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertNotIn(self.name(1), {name for _, name, _ in results})

        # The breaker went OPEN -> HALF_OPEN (trial) -> OPEN (trial failed),
        # and the new cooldown started when the trial failed.
        self.assertEqual(
            transitions,
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, OPEN)]
        )
        self.assertEqual(self.state(1), OPEN)
        self.assertEqual(self.backends[1].breaker.snapshot()["times_opened"], 2)

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
