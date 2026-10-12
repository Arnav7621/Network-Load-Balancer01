import os
import sys
import threading
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "lb"
    )
)

from circuit_breaker import (
    CircuitBreaker,
    CLOSED,
    OPEN,
    HALF_OPEN
)


class FakeClock:

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class CircuitBreakerTests(unittest.TestCase):

    def setUp(self):

        self.clock = FakeClock()
        self.events = []

        self.cb = CircuitBreaker(
            "b",
            failure_threshold=3,
            open_seconds=10,
            clock=self.clock,
            on_state_change=lambda name, old, new:
                self.events.append((old, new))
        )

    def fail(self, count):

        for _ in range(count):

            self.assertTrue(
                self.cb.acquire()
            )

            self.cb.record_failure()

    def test_opens_after_threshold(self):

        self.fail(2)

        self.assertEqual(
            self.cb.snapshot()["state"],
            CLOSED
        )

        self.fail(1)

        self.assertEqual(
            self.cb.snapshot()["state"],
            OPEN
        )

        self.assertEqual(
            self.events,
            [(CLOSED, OPEN)]
        )

    def test_success_resets_failure_count(self):

        self.fail(2)

        self.cb.acquire()
        self.cb.record_success()

        self.fail(2)

        self.assertEqual(
            self.cb.snapshot()["state"],
            CLOSED
        )

    def test_open_blocks_requests(self):

        self.fail(3)

        self.assertFalse(
            self.cb.is_available()
        )

        self.assertFalse(
            self.cb.acquire()
        )

    def test_half_open_after_cooldown_allows_one_probe(self):

        self.fail(3)

        self.clock.advance(10)

        self.assertTrue(
            self.cb.is_available()
        )

        self.assertTrue(
            self.cb.acquire()
        )

        self.assertEqual(
            self.cb.snapshot()["state"],
            HALF_OPEN
        )

        self.assertFalse(
            self.cb.acquire()
        )

    def test_probe_success_closes(self):

        self.fail(3)

        self.clock.advance(10)

        self.cb.acquire()
        self.cb.record_success()

        self.assertEqual(
            self.cb.snapshot()["state"],
            CLOSED
        )

    def test_probe_failure_reopens(self):

        self.fail(3)

        self.clock.advance(10)

        self.cb.acquire()
        self.cb.record_failure()

        self.assertEqual(
            self.cb.snapshot()["state"],
            OPEN
        )

        self.assertFalse(
            self.cb.is_available()
        )


class CircuitBreakerRecoveryTests(unittest.TestCase):
    """CLOSED -> OPEN -> HALF_OPEN -> CLOSED, driven by a fake clock.

    Nothing here waits for real time: the cooldown passes only when the
    test calls clock.advance().
    """

    def setUp(self):
        self.clock = FakeClock()
        self.events = []

    def make(self, **settings):
        options = dict(
            failure_threshold=3,
            open_seconds=10,
            half_open_max_probes=1,
            success_threshold=1,
        )
        options.update(settings)

        return CircuitBreaker(
            "b",
            clock=self.clock,
            on_state_change=lambda name, old, new:
                self.events.append((old, new)),
            **options
        )

    def open_circuit(self, breaker):
        for _ in range(breaker.failure_threshold):
            self.assertTrue(breaker.acquire())
            breaker.record_failure()

        self.assertEqual(breaker.snapshot()["state"], OPEN)

    def test_full_recovery_cycle_passes_through_every_state(self):
        breaker = self.make()

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        self.assertEqual(breaker.snapshot()["state"], HALF_OPEN)

        breaker.record_success()

        snapshot = breaker.snapshot()
        self.assertEqual(snapshot["state"], CLOSED)
        self.assertEqual(snapshot["consecutive_failures"], 0)
        self.assertEqual(snapshot["times_opened"], 1)
        self.assertTrue(breaker.is_available())
        self.assertEqual(
            self.events,
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, CLOSED)]
        )

    def test_circuit_stays_open_until_cooldown_is_complete(self):
        breaker = self.make()

        self.open_circuit(breaker)

        self.clock.advance(9)

        self.assertFalse(breaker.is_available())
        self.assertFalse(breaker.acquire())
        self.assertEqual(breaker.snapshot()["state"], OPEN)

        self.clock.advance(1)                      # exactly 10 s in total

        self.assertTrue(breaker.is_available())
        self.assertTrue(breaker.acquire())

    def test_failed_probe_reopens_circuit_and_restarts_cooldown(self):
        breaker = self.make()

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        breaker.record_failure()                   # the trial request failed

        self.assertEqual(breaker.snapshot()["state"], OPEN)
        self.assertEqual(breaker.snapshot()["times_opened"], 2)
        self.assertEqual(
            self.events,
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, OPEN)]
        )

        # The cooldown is measured from the moment the probe failed.
        self.clock.advance(9)
        self.assertFalse(breaker.is_available())

        self.clock.advance(1)
        self.assertTrue(breaker.is_available())

    def test_only_one_probe_is_allowed_at_a_time(self):
        breaker = self.make()

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())         # the probe
        self.assertFalse(breaker.is_available())   # routing skips the backend
        self.assertFalse(breaker.acquire())        # no second probe

        breaker.record_success()

        self.assertTrue(breaker.acquire())         # closed again

    def test_probe_limit_is_configurable(self):
        breaker = self.make(half_open_max_probes=2)

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        self.assertTrue(breaker.acquire())
        self.assertFalse(breaker.acquire())
        self.assertFalse(breaker.is_available())

    def test_several_successful_probes_are_needed_when_configured(self):
        breaker = self.make(success_threshold=2)

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        breaker.record_success()

        # One success is not enough: still HALF_OPEN, ready for another probe.
        self.assertEqual(breaker.snapshot()["state"], HALF_OPEN)
        self.assertTrue(breaker.is_available())

        self.assertTrue(breaker.acquire())
        breaker.record_success()

        self.assertEqual(breaker.snapshot()["state"], CLOSED)
        self.assertEqual(
            self.events,
            [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, CLOSED)]
        )

    def test_failure_after_a_successful_probe_reopens_circuit(self):
        breaker = self.make(success_threshold=2)

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        breaker.record_success()
        self.assertTrue(breaker.acquire())
        breaker.record_failure()

        self.assertEqual(breaker.snapshot()["state"], OPEN)
        self.assertEqual(breaker.snapshot()["times_opened"], 2)

    def test_late_results_while_open_are_ignored(self):
        # A request that started before the circuit opened may finish after
        # it. Its result must not change the state.
        breaker = self.make()

        self.open_circuit(breaker)

        breaker.record_success()
        breaker.record_failure()

        self.assertEqual(breaker.snapshot()["state"], OPEN)
        self.assertEqual(breaker.snapshot()["times_opened"], 1)
        self.assertEqual(self.events, [(CLOSED, OPEN)])

    def test_only_one_of_many_concurrent_requests_becomes_the_probe(self):
        breaker = self.make()

        self.open_circuit(breaker)
        self.clock.advance(10)

        threads_count = 20
        barrier = threading.Barrier(threads_count)
        results = []
        lock = threading.Lock()

        def try_acquire():
            barrier.wait(timeout=5)                # all start together
            allowed = breaker.acquire()

            with lock:
                results.append(allowed)

        threads = [
            threading.Thread(target=try_acquire)
            for _ in range(threads_count)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(results), threads_count)
        self.assertEqual(results.count(True), 1)
        self.assertEqual(breaker.snapshot()["state"], HALF_OPEN)
        self.assertEqual(
            self.events, [(CLOSED, OPEN), (OPEN, HALF_OPEN)]
        )

    def test_snapshot_reports_remaining_cooldown(self):
        breaker = self.make()

        self.open_circuit(breaker)
        self.assertEqual(breaker.snapshot()["retry_in"], 10.0)

        self.clock.advance(4)
        self.assertEqual(breaker.snapshot()["retry_in"], 6.0)

        self.clock.advance(6)
        snapshot = breaker.snapshot()

        # Current behaviour: the state only moves to HALF_OPEN when a
        # request arrives, so an idle breaker still reports OPEN with 0 s
        # left until then.
        self.assertEqual(snapshot["state"], OPEN)
        self.assertEqual(snapshot["retry_in"], 0.0)

        self.assertTrue(breaker.acquire())
        self.assertEqual(breaker.snapshot()["state"], HALF_OPEN)

    def test_reset_closes_an_open_circuit(self):
        breaker = self.make()

        self.open_circuit(breaker)
        breaker.reset()

        self.assertEqual(breaker.snapshot()["state"], CLOSED)
        self.assertTrue(breaker.is_available())
        self.assertEqual(self.events, [(CLOSED, OPEN), (OPEN, CLOSED)])

    def test_failing_state_change_callback_does_not_break_the_breaker(self):
        def broken_callback(name, old, new):
            raise RuntimeError("dashboard exploded")

        breaker = CircuitBreaker(
            "b",
            failure_threshold=3,
            open_seconds=10,
            clock=self.clock,
            on_state_change=broken_callback
        )

        self.open_circuit(breaker)
        self.clock.advance(10)

        self.assertTrue(breaker.acquire())
        breaker.record_success()

        self.assertEqual(breaker.snapshot()["state"], CLOSED)


if __name__ == "__main__":
    unittest.main()
