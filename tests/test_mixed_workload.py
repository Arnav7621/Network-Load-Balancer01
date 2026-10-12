"""Tests for normal and slow requests arriving together.

A slow request is held inside a fake backend (FakeBackend.gate) until the
test releases it, so every test controls exactly when "a slow request is in
flight". No test relies on real elapsed time to decide the outcome.

They check three things:
  * responsiveness: a slow request does not block other clients,
  * routing: how each algorithm distributes a mixed workload,
  * accounting: statistics stay consistent under concurrent mixed traffic.
"""

import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import LoadBalancerTestCase
from algorithms import LeastConnections, RoundRobin, WeightedRoundRobin


class MixedWorkloadTests(LoadBalancerTestCase):

    def release_all(self):
        for fake in self.fakes:
            fake.gate.set()

    # ------------------------------------------------------------------
    # Responsiveness
    # ------------------------------------------------------------------

    def test_fast_requests_complete_while_a_slow_request_is_in_flight(self):
        slow = self.get_async("/slow")
        self.assertTrue(self.fakes[0].slow_started.wait(5))

        fast = self.get_many(12)                   # slow request still held

        self.assertFalse(slow.done())
        self.assertEqual({status for status, _, _ in fast}, {200})

        # Round Robin kept spreading the fast requests over every backend,
        # including the one that is busy with the slow request.
        self.assertEqual(
            {name for _, name, _ in fast},
            {self.name(0), self.name(1), self.name(2)}
        )

        # The slow request is the only thing still counted as active.
        self.wait_until(
            lambda: [b.active_conns for b in self.backends] == [1, 0, 0],
            message="only the slow request to remain active"
        )

        self.fakes[0].gate.set()

        status, served_by, _ = slow.wait()
        self.assertEqual((status, served_by), (200, self.name(0)))

        self.assert_no_leaked_connections()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def test_least_connections_steers_fast_requests_away_from_busy_backend(self):
        self.use_algorithm(LeastConnections())

        slow = self.get_async("/slow")             # idle tie -> backend 0
        self.assertTrue(self.fakes[0].slow_started.wait(5))

        fast = self.get_many(10)

        # Backend 0 is busy, backends 1 and 2 are idle (tie -> backend 1).
        self.assertEqual({status for status, _, _ in fast}, {200})
        self.assertEqual({name for _, name, _ in fast}, {self.name(1)})
        self.assertEqual(self.fakes[0].hits_for("/"), 0)

        self.fakes[0].gate.set()

        status, served_by, _ = slow.wait()
        self.assertEqual((status, served_by), (200, self.name(0)))

        self.assert_no_leaked_connections()

    def test_least_connections_spreads_concurrent_slow_requests(self):
        self.use_algorithm(LeastConnections())

        requests = []

        for index in range(3):
            requests.append(self.get_async("/slow"))

            # Wait until this request is inside its backend before sending
            # the next one, so each sees the previous one as "active".
            self.assertTrue(self.fakes[index].slow_started.wait(5))

        # Each backend received exactly one of the three slow requests.
        for fake in self.fakes:
            self.assertEqual(fake.hits_for("/slow"), 1)

        self.assertEqual(
            [b.active_conns for b in self.backends], [1, 1, 1]
        )

        self.release_all()

        results = [request.wait() for request in requests]

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertEqual(
            {name for _, name, _ in results},
            {self.name(0), self.name(1), self.name(2)}
        )

        self.assert_no_leaked_connections()

    def test_least_connections_sends_idle_sequential_requests_to_first_backend(self):
        # Documented behaviour: with no overlap every backend has 0 active
        # connections, so the tie always goes to the first backend. Showing
        # Least Connections spreading traffic needs concurrent slow
        # requests (see the test above).
        self.use_algorithm(LeastConnections())

        results = self.get_many(15)

        self.assertEqual({name for _, name, _ in results}, {self.name(0)})

    def test_round_robin_distributes_a_mixed_workload_evenly(self):
        self.release_all()                         # /slow answers at once

        results = [
            self.get("/" if index % 2 == 0 else "/slow")
            for index in range(60)
        ]

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertEqual(
            Counter(name for _, name, _ in results),
            {self.name(0): 20, self.name(1): 20, self.name(2): 20}
        )

    def test_weighted_round_robin_distributes_a_mixed_workload_by_weight(self):
        self.release_all()

        for backend, weight in zip(self.backends, (3, 2, 1)):
            backend.weight = weight

        self.use_algorithm(WeightedRoundRobin())

        results = [
            self.get("/" if index % 2 == 0 else "/slow")
            for index in range(60)
        ]

        self.assertEqual({status for status, _, _ in results}, {200})
        self.assertEqual(
            Counter(name for _, name, _ in results),
            {self.name(0): 30, self.name(1): 20, self.name(2): 10}
        )

    # ------------------------------------------------------------------
    # Accounting under concurrency
    # ------------------------------------------------------------------

    def test_statistics_stay_consistent_under_concurrent_mixed_traffic(self):
        self.release_all()

        requests = [
            self.get_async("/" if index % 3 else "/slow")
            for index in range(30)
        ]

        results = [request.wait() for request in requests]

        fast_count = sum(1 for index in range(30) if index % 3)
        slow_count = 30 - fast_count

        self.assertEqual({status for status, _, _ in results}, {200})

        # What the clients saw...
        seen = Counter(name for _, name, _ in results)

        # ...matches what the load balancer and the backends recorded.
        for index, backend in enumerate(self.backends):
            self.assertEqual(backend.total_requests, seen[self.name(index)])
            self.assertEqual(backend.errors, 0)

        self.assertEqual(sum(b.total_requests for b in self.backends), 30)
        self.assertEqual(sum(f.hits for f in self.fakes), 30)
        self.assertEqual(sum(f.hits_for("/") for f in self.fakes), fast_count)
        self.assertEqual(sum(f.hits_for("/slow") for f in self.fakes), slow_count)

        self.assert_no_leaked_connections()


if __name__ == "__main__":
    unittest.main()
