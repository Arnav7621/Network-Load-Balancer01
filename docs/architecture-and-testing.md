# Architecture and test strategy

## Components

| Part | File | Responsibility |
|---|---|---|
| Load balancer | `lb/balancer.py` | HTTP server on port 8080: `/stats`, `/algorithm`, and the reverse proxy with retry and failover. Also runs the health-check thread |
| Routing and backend state | `lb/algorithms.py` | `Backend` (health, statistics, circuit breaker) and the three algorithms |
| Circuit breaker | `lb/circuit_breaker.py` | The CLOSED / OPEN / HALF_OPEN state machine, thread-safe, with an injectable clock |
| Backend | `backend/app.py` | Flask server: `/`, `/health`, `/slow` |
| Dashboard | `dashboard/app.py`, `templates/`, `static/` | Flask server that serves the page and forwards `/api/stats` and `/api/algorithm` to the load balancer |

The load balancer uses Python's `ThreadingHTTPServer`: each request is handled
in its own thread, so a slow request never blocks other clients. It talks to
backends with `http.client`.

## Life of a request

1. The client sends `GET /path` to the load balancer (port 8080).
2. `/stats` and `/algorithm` are answered by the load balancer itself. Anything
   else is proxied.
3. A time budget is started for the request (the request deadline, 20 s).
4. The selected algorithm picks a backend from the **routable** ones: healthy
   according to the health checker **and** with a circuit that allows traffic.
5. The circuit breaker is asked for permission (`acquire()`). A circuit in
   HALF_OPEN lets exactly one trial request through.
6. The load balancer connects to the backend (connect timeout, 1 s), sends the
   request, and waits for the response (read timeout, 10 s, limited by what is
   left of the budget).
7. If the backend answered, the outcome is recorded (a 5xx counts as a failure
   for the circuit breaker and the error count), the active-connection count
   is lowered, and the response is returned to the client with an
   `X-Served-By` header. **From this point the request is never retried.**
8. If the backend could not answer, the failure is recorded and, if repeating
   the request is safe, another backend that has not been tried is picked
   (back to step 4). Otherwise the client gets an error.
9. When no backend is left, the client gets **503** (none available), **504**
   (the last attempt timed out) or **502** (a request that is not safe to
   repeat failed after it was sent).

## Health checks and circuit breaker: two separate mechanisms

| | Health checker | Circuit breaker |
|---|---|---|
| Kind | Active: probes `GET /health` itself | Passive: watches real client requests |
| Trigger | 3 failed checks in a row (every 2 s, 2 s timeout) | 3 failed requests in a row |
| Effect | Backend marked DOWN | Circuit OPEN for 10 s, then one trial request |
| Recovery | 2 successful checks in a row | The trial request succeeds |
| Works without traffic | Yes | No, it needs requests |

They write different fields (`healthy` and the breaker state) and meet in one
place: `Backend.routable()`. A stopped backend is usually noticed first by the
circuit breaker (after three requests); a hung backend is slower to notice
because every failed attempt costs a read timeout.

## What happens when something fails

| Situation | What Vantora does | What the client sees |
|---|---|---|
| Backend stopped (connection refused) | Fails over immediately; circuit opens after 3 failures; health checker marks it DOWN after about 6 s | 200 from another backend |
| Backend hung (accepts, never answers) | Waits one read timeout per attempt, then fails over; circuit opens after 3 failures | 200 from another backend, after a delay |
| Backend slow but healthy (within the read timeout) | Waits, no retry, no failure | 200 |
| Backend answers 5xx | Counts a failure (breaker and error count); returns the answer, does not retry | The 5xx |
| All backends unavailable | Nothing to try | 503 |
| Every backend times out, or the deadline runs out | Stops trying | 504 |
| Client disconnects while the response is written | Logged; not retried; not blamed on the backend | – |
| A stopped backend returns | Two good health checks, cooldown passes, trial request succeeds | Traffic resumes |

## Test strategy

Run everything from the project root:

```bash
python3 -m unittest discover -s tests -v
```

**Last recorded result:** `Ran 107 tests ... OK` (12 October 2026, Python 3.12,
with Flask, PyYAML and scapy installed so that no test was skipped). Run it on
your own computer and record your own result before submitting.

| Layer | Files | What it checks |
|---|---|---|
| Unit | `test_circuit_breaker.py` (18), `test_routing_algorithms.py` (14), `test_backend_health.py` (6) | The state machine with a fake clock (full recovery cycle, cooldown, probe limits, concurrency, late results), the algorithms and exclusion of unhealthy or open-circuit backends, the health-state thresholds |
| Integration, real load balancer | `test_failover.py` (8), `test_slow_requests.py` (16), `test_mixed_workload.py` (7) | The real request handler in front of fake backends on free ports: failover, circuit open and recovery, slow and hung backends, timeouts, the retry policy, 502/503/504, a client disconnecting, normal and slow requests together, statistics under concurrency |
| Components | `test_dashboard.py` (12), `test_backend_app.py` (5) | The dashboard's API and configuration (debugger off by default, no hardcoded address, clear errors), the backend's `/slow?ms=` handling |
| Configuration and evidence | `test_compose_config.py` (11), `test_benchmark_report.py` (3), `test_wireshark_tools.py` (7) | `docker-compose.yml` consistency (service names, ports, health checks, no `localhost` between containers), the benchmark report against its CSV files, the traffic generator and capture analyzer |

How the tests avoid depending on timing (`tests/helpers.py`):

- **A fake clock** drives circuit-breaker cooldowns, so no test waits for ten
  seconds to pass.
- **Gated fake backends** hold a `/slow` response until the test releases it,
  so "a slow request is in flight" is a state the test controls.
- **Free ports** (port 0) for every server, and servers are shut down with
  `addCleanup()`, so nothing clashes with a running Vantora and cleanup
  happens even when an assertion fails.
- **Patched timeouts** (a fraction of a second) run the timeout paths quickly.
  A few tests include a short real wait (for example 0.3 s) only to make a
  "has not failed yet" assertion stronger; a slow computer makes those
  assertions easier to pass, not harder.
- The health-check thread is never started in tests; health results are fed
  in directly.

How the tests were checked: the important behaviours were verified by
deliberately breaking the code (for example removing the deadline, retrying
unsafe requests, reverting the read timeout to 3 s, putting the debugger back
on, editing the report) and confirming that tests fail. All 15 such changes
were caught, and the test for a client disconnecting fails on the original
code. This was a one-off check, not something the suite does by itself.

### Not covered by automated tests

- **The health-check loop's timing.** The rules (3 failures, 2 successes) are
  tested; the loop that runs them every 2 seconds is not.
- **Docker at runtime.** `docker-compose.yml` is checked statically only.
  Start the stack yourself and check `docker compose ps`.
- **The dashboard in a browser.** The server side is tested; the JavaScript was
  only syntax-checked, so check the page visually (see the checklist).
- **A hung backend with the default 10 s timeout.** Tested with shortened
  timeouts. Measured by hand: with a frozen backend each affected request took
  10.0 s with the defaults and 2.0 s with `VANTORA_READ_TIMEOUT=2`.
- **Packet captures.** The tools are tested; recording the captures is manual.
- **Heavy load.** That is what the Locust benchmark is for.
- **Python 3.9.** Syntax compatibility was checked, but the suite was run on
  Python 3.12.
