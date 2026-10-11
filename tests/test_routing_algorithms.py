import unittest
import sys
import os
from unittest import mock

# Allow Python to import files from the lb folder
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "lb")
    )
)

from algorithms import (
    Backend,
    RoundRobin,
    WeightedRoundRobin,
    LeastConnections
)
import algorithms
from circuit_breaker import OPEN


class TestRoutingAlgorithms(unittest.TestCase):

    def setUp(self):
        # The circuit breaker prints a line on every state change; keep
        # the test output readable.
        patcher = mock.patch.object(
            algorithms,
            "print",
            mock.MagicMock(),
            create=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.backends = [
            Backend("127.0.0.1", 9001, weight=3),
            Backend("127.0.0.1", 9002, weight=2),
            Backend("127.0.0.1", 9003, weight=1)
        ]

    def test_round_robin(self):
        router = RoundRobin()

        selected_ports = []

        for _ in range(6):
            backend = router.pick(self.backends)
            selected_ports.append(backend.port)

        expected = [
            9001,
            9002,
            9003,
            9001,
            9002,
            9003
        ]

        self.assertEqual(selected_ports, expected)

    def test_weighted_round_robin(self):
        router = WeightedRoundRobin()

        selected_ports = []

        for _ in range(12):
            backend = router.pick(self.backends)
            selected_ports.append(backend.port)

        self.assertEqual(selected_ports.count(9001), 6)
        self.assertEqual(selected_ports.count(9002), 4)
        self.assertEqual(selected_ports.count(9003), 2)

    def test_least_connections(self):
        router = LeastConnections()

        self.backends[0].active_conns = 5
        self.backends[1].active_conns = 2
        self.backends[2].active_conns = 8

        selected = router.pick(self.backends)

        self.assertEqual(selected.port, 9002)


    # ------------------------------------------------------------------
    # Unhealthy backends, open circuits and "nothing routable"
    # ------------------------------------------------------------------

    def mark_unhealthy(self, backend):
        # Failed health checks are reported the same way the health
        # monitor reports them.
        for _ in range(3):
            backend.record_health_failure()

        self.assertFalse(backend.healthy)

    def open_circuit(self, backend):
        # Real request failures are reported to the real circuit breaker.
        for _ in range(backend.breaker.failure_threshold):
            self.assertTrue(backend.breaker.acquire())
            backend.breaker.record_failure()

        self.assertEqual(backend.breaker.snapshot()["state"], OPEN)

    def all_routers(self):
        return [
            ("Round Robin", RoundRobin()),
            ("Weighted Round Robin", WeightedRoundRobin()),
            ("Least Connections", LeastConnections())
        ]

    def test_round_robin_skips_unhealthy_backend(self):
        router = RoundRobin()

        self.mark_unhealthy(self.backends[1])

        selected_ports = [
            router.pick(self.backends).port
            for _ in range(6)
        ]

        self.assertEqual(
            selected_ports,
            [9001, 9003, 9001, 9003, 9001, 9003]
        )

    def test_weighted_round_robin_skips_unhealthy_backend_and_keeps_weights(self):
        router = WeightedRoundRobin()

        # 9001 (weight 3) is DOWN, so 9002 (weight 2) and 9003 (weight 1)
        # must share traffic 2:1.
        self.mark_unhealthy(self.backends[0])

        selected_ports = [
            router.pick(self.backends).port
            for _ in range(6)
        ]

        self.assertNotIn(9001, selected_ports)
        self.assertEqual(selected_ports.count(9002), 4)
        self.assertEqual(selected_ports.count(9003), 2)

    def test_least_connections_skips_unhealthy_backend_with_fewest_connections(self):
        router = LeastConnections()

        self.backends[0].active_conns = 5
        self.backends[1].active_conns = 0   # fewest, but unhealthy
        self.backends[2].active_conns = 8

        self.mark_unhealthy(self.backends[1])

        self.assertEqual(router.pick(self.backends).port, 9001)

    def test_backend_with_open_circuit_is_never_selected(self):
        for name, router in self.all_routers():
            with self.subTest(algorithm=name):
                backends = [
                    Backend("127.0.0.1", 9001, weight=3),
                    Backend("127.0.0.1", 9002, weight=2),
                    Backend("127.0.0.1", 9003, weight=1)
                ]

                # 9002 would be the least-connections choice if its
                # circuit were closed.
                backends[0].active_conns = 3
                backends[1].active_conns = 0
                backends[2].active_conns = 3

                self.open_circuit(backends[1])

                selected_ports = [
                    router.pick(backends).port
                    for _ in range(12)
                ]

                self.assertNotIn(9002, selected_ports)
                self.assertTrue(
                    {9001, 9003}.issuperset(selected_ports)
                )

    def test_backend_needs_healthy_status_and_closed_circuit_to_be_routable(self):
        healthy_closed = Backend("127.0.0.1", 9001)

        unhealthy_closed = Backend("127.0.0.1", 9002)
        self.mark_unhealthy(unhealthy_closed)

        healthy_open = Backend("127.0.0.1", 9003)
        self.open_circuit(healthy_open)

        unhealthy_open = Backend("127.0.0.1", 9004)
        self.mark_unhealthy(unhealthy_open)
        self.open_circuit(unhealthy_open)

        self.assertTrue(healthy_closed.routable())
        self.assertFalse(unhealthy_closed.routable())
        self.assertFalse(healthy_open.routable())
        self.assertFalse(unhealthy_open.routable())

    def test_recovered_backend_is_selected_again(self):
        router = RoundRobin()

        self.mark_unhealthy(self.backends[1])

        before = [router.pick(self.backends).port for _ in range(4)]
        self.assertNotIn(9002, before)

        # Two consecutive successful health checks bring it back.
        self.backends[1].record_health_success()
        self.backends[1].record_health_success()

        self.assertTrue(self.backends[1].healthy)

        after = [router.pick(self.backends).port for _ in range(6)]
        self.assertIn(9002, after)

    def test_pick_returns_none_when_every_backend_is_unhealthy(self):
        for backend in self.backends:
            self.mark_unhealthy(backend)

        for name, router in self.all_routers():
            with self.subTest(algorithm=name):
                self.assertIsNone(router.pick(self.backends))

    def test_pick_returns_none_when_every_circuit_is_open(self):
        for backend in self.backends:
            self.open_circuit(backend)

        for name, router in self.all_routers():
            with self.subTest(algorithm=name):
                self.assertIsNone(router.pick(self.backends))

    def test_pick_returns_none_for_mix_of_unhealthy_and_open_backends(self):
        self.mark_unhealthy(self.backends[0])
        self.open_circuit(self.backends[1])
        self.mark_unhealthy(self.backends[2])

        for name, router in self.all_routers():
            with self.subTest(algorithm=name):
                self.assertIsNone(router.pick(self.backends))

    def test_pick_returns_none_for_empty_backend_list(self):
        for name, router in self.all_routers():
            with self.subTest(algorithm=name):
                self.assertIsNone(router.pick([]))


if __name__ == "__main__":
    unittest.main()
