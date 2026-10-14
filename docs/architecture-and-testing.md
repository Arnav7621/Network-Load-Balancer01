# Architecture and Test Strategy

## Components

| Part | File | Responsibility |
|---|---|---|
| Load balancer | `lb/balancer.py` | HTTP server on port 8080, `/stats`, `/algorithm`, reverse proxy, retry, failover, and health-check thread |
| Routing and backend state | `lb/algorithms.py` | Backend health, statistics, circuit breaker integration, and three routing algorithms |
| Circuit breaker | `lb/circuit_breaker.py` | Thread-safe CLOSED / OPEN / HALF_OPEN state machine with an injectable clock |
| Backend | `backend/app.py` | Flask server providing `/`, `/health`, and `/slow` |
| Dashboard | `dashboard/app.py`, `dashboard/templates/`, `dashboard/static/` | Monitoring interface that displays backend health, request statistics, routing algorithm, and events; forwards `/api/stats` and `/api/algorithm` to the load balancer |

The load balancer uses Python's `ThreadingHTTPServer`, which handles requests in separate threads so a slow request does not block every other client. It communicates with backend servers using Python's `http.client`.

## Life of a Request

1. The client sends an HTTP GET request to the load balancer on port 8080.
2. Requests to `/stats` and `/algorithm` are handled by the load balancer itself. Other supported requests are proxied to a backend.
3. A request deadline of 20 seconds is established.
4. The selected routing algorithm chooses a routable backend. A backend must be healthy according to the health checker, and its circuit breaker must permit traffic.
5. The circuit breaker is checked using `acquire()`. When a circuit is HALF_OPEN, only one trial request is permitted.
6. The load balancer connects to the selected backend with a 1-second connection timeout and waits for the response with a 10-second read timeout, subject to the remaining request deadline.
7. If the backend responds, the result is recorded, the active-connection count is updated, and the response is returned to the client with an `X-Served-By` header. The response is not retried after it has been received.
8. If the backend cannot respond, the failure is recorded. If retrying is safe, the load balancer selects another backend that has not already been tried.
9. If no backend can complete the request, the client receives an appropriate error response: HTTP 503 when no backend is available, HTTP 504 when attempts time out, or HTTP 502 for certain failed requests that cannot safely be retried.

## Health Checks and Circuit Breaker

Health checks and circuit breakers are separate mechanisms that work together to prevent traffic from being sent to unavailable backends.

| Property | Health Checker | Circuit Breaker |
|---|---|---|
| Type | Active monitoring | Passive monitoring |
| How it works | Periodically sends `GET /health` requests | Observes failures during client requests |
| Trigger | Three consecutive failed checks | Three consecutive failed requests |
| Effect | Marks the backend DOWN | Opens the circuit for 10 seconds |
| Recovery | Two consecutive successful checks | A successful trial request after the cooldown |
| Works without client traffic | Yes | No |

The health checker updates the backend's health state, while the circuit breaker maintains its own state. These conditions are combined by `Backend.routable()`.

A stopped backend can trigger the circuit breaker after repeated client-request failures. The health checker independently marks the backend DOWN after consecutive failed probes.

## Failure Handling

| Situation | Vantora's Response | Expected Client Result |
|---|---|---|
| Backend is stopped or refuses a connection | Attempts another eligible backend and records the failure | HTTP 200 if another backend responds successfully |
| Backend accepts a connection but does not respond | Waits for the configured timeout, then attempts failover when permitted | HTTP 200 if another backend responds within the deadline |
| Backend is slow but responds within the timeout | Waits for the response without retrying | HTTP 200 |
| Backend returns a 5xx response | Records the failure for statistics and circuit-breaker purposes; returns the backend response | Original 5xx response |
| All backends are unavailable | Stops routing attempts | HTTP 503 |
| Attempts time out or the request deadline expires | Stops trying after the available time budget is exhausted | HTTP 504 |
| Client disconnects during response delivery | Logs the disconnect and does not retry the completed backend request | Connection ends |
| A stopped backend returns | Health checks and circuit-breaker recovery permit traffic to resume | Successful responses once the backend becomes routable |

## Test Strategy

Run the automated tests from the project root:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### Latest Verified Test Results

**Test date:** 13 October 2026

**Environment:** Project virtual environment (`.venv`)

**Command:** `.venv/bin/python -m unittest discover -s tests -v`

**Result:** `Ran 111 tests in 5.924s — OK`

All 111 tests passed with no failures or skipped tests in the recorded run.

### Runtime Verification

On 13 October 2026, the Docker deployment was checked using `docker compose ps`. All five services reported healthy:

| Service | Port | Verified Status |
|---|---:|---|
| Backend 1 | 9001 | Healthy |
| Backend 2 | 9002 | Healthy |
| Backend 3 | 9003 | Healthy |
| Load balancer | 8080 | Healthy |
| Monitoring dashboard | 5050 | Healthy |

Additional HTTP checks confirmed that the load balancer returned HTTP 200 and the dashboard returned HTTP 200. The `/stats` endpoint showed all three backends UP, with their circuit breakers CLOSED, during that check.

These results document the observed test and runtime verification runs. They do not imply that every possible workload or failure condition has been tested.

### Test Coverage

| Layer | Test Files | What They Check |
|---|---|---|
| Unit tests | `test_circuit_breaker.py`, `test_routing_algorithms.py`, `test_backend_health.py` | Circuit-breaker state transitions, cooldown, trial-request limits, routing algorithms, backend eligibility, and health-state thresholds |
| Integration tests | `test_failover.py`, `test_slow_requests.py`, `test_mixed_workload.py` | Failover, circuit opening and recovery, slow and hung backends, timeouts, retry policy, HTTP 502/503/504 handling, client disconnects, concurrent workloads, and statistics |
| Component tests | `test_dashboard.py`, `test_backend_app.py` | Dashboard API and configuration, debugger settings, error handling, backend endpoints, and `/slow?ms=` behavior |
| Configuration and evidence tests | `test_compose_config.py`, `test_benchmark_report.py`, `test_wireshark_tools.py` | Docker Compose consistency, service names, ports, health checks, benchmark-report consistency with CSV files, traffic generation, and capture analysis |
| Dashboard assets | `test_dashboard_assets.py` | Dashboard HTML, CSS, and JavaScript asset checks |

### Test Design

The test suite uses several techniques to reduce flaky timing behavior:

- **Fake clock:** Circuit-breaker cooldowns can be tested without waiting for the full cooldown period.
- **Gated fake backends:** Tests control when a slow response is released, making it possible to test requests that remain in flight.
- **Free ports:** Test servers bind to available ports to avoid conflicts with the running application.
- **Cleanup hooks:** Servers are shut down even when assertions fail.
- **Shortened timeouts:** Timeout behavior is tested quickly without waiting for the production timeout duration.
- **Controlled health state:** Health-check outcomes are injected directly into relevant tests instead of depending on the real background health-check thread.

Important behaviors were also checked by deliberately introducing selected faults and verifying that the tests detected them. This was a one-time validation exercise rather than an automatic part of every test run.

## Limitations of Automated Testing

- **Health-check timing:** The health-state thresholds are tested, but the timing behavior of the complete background health-check loop is not fully covered by automated tests.
- **Docker runtime:** Automated tests validate the Docker Compose configuration but do not fully test container behavior. Manual verification on 13 October 2026 confirmed that all five services were healthy.
- **Dashboard browser behavior:** Server-side dashboard behavior and asset checks are covered by tests. Visual appearance and interactive behavior should also be checked manually in a browser.
- **Default hung-backend timeout:** Hung-backend behavior is tested with shortened timeouts. A manual measurement with a frozen backend previously recorded approximately 10 seconds using the default read timeout and 2 seconds with `VANTORA_READ_TIMEOUT=2`.
- **Packet captures:** Capture-generation and analysis tools are tested, but recording packet captures is a manual activity.
- **Heavy load:** The Locust benchmark provides a limited performance evaluation. It does not establish production-scale capacity.
- **Python versions:** Python 3.9 syntax compatibility was previously checked. The latest 111-test result recorded here was run in the project's `.venv`; confirm the exact Python version with `.venv/bin/python --version` if it must be specified in the submission report.

## Performance Evaluation

The project includes Locust benchmark data for Round Robin, Weighted Round Robin, and Least Connections under the same general workload.

The results include throughput, response-time comparisons, request outcomes, and raw CSV data in `docs/load-testing/`.

The benchmark is limited to one recorded run per algorithm on a single computer with identical backend configurations. It should be presented as an experimental observation, not as proof that one algorithm is universally faster or better than the others.

Least Connections is expected to be most informative when requests overlap in time. With sequential, short requests, the first backend can repeatedly have the lowest active-connection count, so the workload must be considered when interpreting its results.

## Wireshark Evidence

The `docs/wireshark/` directory contains packet captures for:

- Round Robin distribution
- Weighted Round Robin distribution
- Concurrent Least Connections traffic
- Failover while Backend 9002 is down
- Recovery after Backend 9002 returns

These captures support analysis of request distribution, separate client-to-load-balancer and load-balancer-to-backend connections, and failure/recovery behavior.

Docker Desktop's internal container traffic is not necessarily visible directly on the host's network interface. The capture environment and method should therefore be stated accurately when discussing the evidence.

## Conclusion

Vantora demonstrates HTTP reverse-proxy load balancing across three backend servers using Round Robin, Weighted Round Robin, and Least Connections. It includes backend health monitoring, circuit breaking, retry and failover behavior, a monitoring dashboard, automated tests, load-testing artifacts, and packet-capture evidence.

The latest recorded verification includes 111 passing automated tests, successful HTTP checks for the load balancer and dashboard, and five healthy Docker services. The documented benchmark and test limitations should be considered when interpreting the results.
