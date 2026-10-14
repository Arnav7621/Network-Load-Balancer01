# Vantora Submission Checklist

Evidence is marked **Complete** when it exists and has been checked, **Complete, limited** when it exists with a documented limitation, or **Needs your action** when additional evidence or work is required.

## Artifacts to Submit

| # | Artifact | Location | Status |
|---|---|---|---|
| 1 | Source code for load balancer, backend services, and dashboard | `lb/`, `backend/`, `dashboard/` | Complete |
| 2 | README covering setup, execution, Docker, endpoints, configuration, and limitations | `README.md` | Complete |
| 3 | Problem statement and objectives | This document, below | Complete — include in the final report |
| 4 | Architecture, request flow, failure handling, and test strategy | `docs/architecture-and-testing.md` | Complete — updated with latest verification |
| 5 | Protocol and message design, endpoints, status codes, and headers | `README.md`, `docs/architecture-and-testing.md` | Complete |
| 6 | Docker Compose deployment with three backends, load balancer, and dashboard | `docker-compose.yml`, Dockerfiles | Complete — all five services were verified healthy on 13 October 2026 |
| 7 | Automated tests and test results | `tests/` | Complete — 111 tests passed in 5.924 seconds, with no failures or skips |
| 8 | Test report for normal and failure conditions | `docs/architecture-and-testing.md`, demonstration results | Complete, limited — record individual scenario results after verifying them |
| 9 | Performance evaluation: throughput, response time, and request outcomes | `docs/load-testing/` | Complete, limited — one recorded run per algorithm; identical backends and a single-computer test environment |
| 10 | Wireshark packet captures | `docs/wireshark/` | Complete, limited — five captures are present; verify that each demonstrates its intended scenario |
| 11 | Wireshark screenshots and analysis | `docs/wireshark/README.md` | Needs your action — confirm the required screenshots have been saved |
| 12 | Dashboard screenshots | Browser screenshots | Needs your action |
| 13 | Five-minute demonstration | Demonstration script below | Needs your action — rehearse once |
| 14 | Individual team contributions | Contribution table below | Needs your action — fill in actual contributions |
| 15 | Viva preparation | Viva notes below | Prepared — review and adapt before the viva |

## Problem Statement and Objectives

Web services need to remain available and responsive as demand increases and servers fail. A single server can become a performance bottleneck and a single point of failure.

Vantora is an HTTP reverse-proxy load balancer that sits in front of three backend servers. Its objectives are to:

1. Distribute HTTP requests using configurable Round Robin, Weighted Round Robin, and Least Connections algorithms.
2. Monitor backend health and avoid routing traffic to unavailable servers.
3. Use circuit breaking, retry, and failover mechanisms to improve service continuity when a backend fails.
4. Provide a monitoring dashboard showing backend status, request statistics, routing algorithms, and events.
5. Evaluate functionality through automated tests, load testing, and Wireshark packet captures.

## Evidence Checklist

### Deployment and Testing

- [x] Source code, README, and architecture documentation available.
- [x] Docker Compose deployment started and all five services confirmed healthy on 13 October 2026.
- [x] Automated test suite executed: 111 tests passed in 5.924 seconds.
- [x] Load balancer returned HTTP 200 during runtime verification.
- [x] Dashboard returned HTTP 200 during runtime verification.
- [x] `/stats` showed all three backends UP and their circuit breakers CLOSED during verification.
- [ ] Save a terminal screenshot of `docker compose ps` showing all five healthy services.
- [ ] Save a terminal screenshot of the automated test run ending with `OK`.
- [ ] Record the exact Python version used for the test run.
- [ ] Verify and record individual normal-operation and failure-scenario results.

### Dashboard Screenshots

- [ ] Dashboard with all three backends healthy.
- [ ] Dashboard while one backend is stopped, showing the actual health and circuit-breaker state.
- [ ] Dashboard after changing the routing algorithm using the selector.
- [ ] Dashboard after backend recovery, showing the backend healthy again.

### Wireshark Evidence

Five packet captures are present in `docs/wireshark/`:

- [x] `round-robin-distribution.pcapng`
- [x] `weighted-distribution.pcapng`
- [x] `least-connections-concurrent.pcapng`
- [x] `failover-backend-9002-down.pcapng`
- [x] `recovery-backend-9002.pcapng`

Additional packet captures and evidence files are also present in the directory.

- [ ] Open each required capture in Wireshark and verify that it shows the intended scenario.
- [ ] Save the required Wireshark screenshots listed in `docs/wireshark/README.md`.
- [ ] Include relevant packet filters, request distribution, failover, and recovery evidence in the report.

### Reporting and Submission

- [ ] Complete the test-results table below with observed results.
- [ ] Complete the team contribution table.
- [ ] Include the load-testing graphs and explain their limitations.
- [ ] Rehearse the five-minute demonstration.
- [ ] Run the final Git checks and commit the completed documentation.
- [ ] Verify the final repository state before submission.

An optional `/slow`-based benchmark may be performed to explore the effect of request duration on routing behavior. Do not claim results from this benchmark unless it has actually been run.

## Test Results Table

Fill in the final column only after performing each scenario and observing its result.

| Scenario | How to Run | Expected Result | Observed Result |
|---|---|---|---|
| Round Robin distribution | `python docs/wireshark/generate_traffic.py --algorithm "Round Robin" --requests 30` | 10 / 10 / 10 requests | Pending final record |
| Weighted Round Robin distribution | `python docs/wireshark/generate_traffic.py --algorithm "Weighted Round Robin" --requests 30` | 15 / 10 / 5 requests | Pending final record |
| Least Connections, sequential requests | `python docs/wireshark/generate_traffic.py --algorithm "Least Connections" --requests 18` | Expected 18 / 0 / 0 when requests do not overlap | Pending final record |
| Least Connections, overlapping requests | `python docs/wireshark/generate_traffic.py --algorithm "Least Connections" --path "/slow?ms=500" --concurrency 6 --requests 18` | All three backends used; approximately 6 / 6 / 6 | Pending final record |
| Slow request | `curl -i "http://localhost:8080/slow"` | HTTP 200 after the configured delay, if within the timeout | Pending final record |
| Backend failure | Stop Backend 9002 and send test requests | Requests continue through available backends; verify circuit and health states | Pending final record |
| Backend recovery | Restart Backend 9002 and wait for recovery conditions | Backend becomes healthy and receives traffic again | Pending final record |
| All backends unavailable | Stop all three backends and send a request | HTTP 503 when no backend is routable | Pending final record |

The traffic generator is located at `docs/wireshark/generate_traffic.py`. Run commands from the project root and use the project's Python environment where needed.

**Important:** The expected results are test expectations, not a substitute for recording actual observations. Check the generator's command-line options with `--help` if any command differs from the current implementation.

## Five-Minute Demonstration

Ensure Docker Compose is running and open the dashboard at `http://localhost:5050`. The load balancer is available at `http://localhost:8080`.

### 1. Introduction — 30 seconds

Explain the problem Vantora solves. Show the architecture diagram, the three backends, the load balancer, and the dashboard.

### 2. Request Distribution — 60 seconds

Demonstrate Round Robin with 30 requests and explain the expected 10 / 10 / 10 distribution. Change the algorithm to Weighted Round Robin and demonstrate the expected 15 / 10 / 5 distribution.

### 3. Least Connections — 45 seconds

Compare sequential requests with overlapping slow requests. Explain that Least Connections is more meaningful when requests overlap because it considers active connections.

### 4. Failover — 90 seconds

Stop Backend 9002 using:

```bash
docker compose stop backend-9002
```

Send requests through the load balancer. Show that available backends continue serving requests, and explain the difference between the circuit breaker and the active health checker.

### 5. Recovery — 30 seconds

Restore Backend 9002:

```bash
docker compose start backend-9002
```

Wait for health checks and circuit-breaker recovery. Verify that the backend becomes healthy and can receive requests again.

### 6. Packet Evidence — 45 seconds

Open an appropriate capture in Wireshark. Explain the client-to-load-balancer and load-balancer-to-backend traffic, request distribution, and the evidence of failover or recovery.

### 7. Results and Limitations — 30 seconds

Show the load-testing graphs and explain what was measured, what the experiment supports, and what it does not prove.

## Viva Notes

**Why use a reverse proxy instead of DNS Round Robin?**

A reverse proxy handles each request and can apply health checks, circuit breaking, retry policies, and connection-aware routing. DNS Round Robin distributes name-resolution responses and cannot provide the same per-request control.

**When should each algorithm be used?**

- **Round Robin:** Suitable when backend capacities and request workloads are similar.
- **Weighted Round Robin:** Useful when backends have different capacities.
- **Least Connections:** Useful when request durations vary and multiple requests overlap.

With sequential short requests, Least Connections can repeatedly choose the first backend because its active-connection count returns to zero between requests.

**Why have both health checks and a circuit breaker?**

Health checks actively probe backends, even without client traffic. A circuit breaker reacts to failures observed during client requests. They monitor different signals and provide complementary protection.

**Why require consecutive failures?**

A single failure may be temporary. Requiring consecutive failures reduces unnecessary backend removal and repeated state changes.

**What is HALF_OPEN?**

After the circuit's cooldown, HALF_OPEN permits a trial request. Its outcome helps determine whether the backend can safely receive traffic again.

**Why use separate connection and read timeouts?**

A connection timeout limits the time spent establishing a connection. A read timeout limits the wait for a response. Separate settings allow an unreachable backend to fail quickly while permitting a slower but healthy backend to respond.

**When is retrying safe?**

Retries are safest when the request has not reached the backend. If a request may already have been processed, repeating it can duplicate side effects. Retry behavior must follow the implementation's method and failure rules.

**What happens if the load balancer itself fails?**

The load balancer is a single point of failure in this design. Redundancy, a virtual IP, or external failover could improve availability, but those are outside the current implementation.

**Is Vantora a Layer 4 or Layer 7 load balancer?**

It is a Layer 7 load balancer because it understands HTTP requests, paths, response status codes, and HTTP headers such as `X-Served-By`.

**What are the benchmark limitations?**

The benchmark has one recorded run per algorithm, identical backends, and a single-computer environment. It should not be used to claim universal performance superiority.

**What does Wireshark demonstrate?**

The captures can demonstrate TCP connections, request routing, the `X-Served-By` header when visible, health probes, and connection failures when a backend is unavailable. Interpret each capture according to its actual packets and capture environment.

## Team Contribution Table

Fill this in with the team's actual work. Do not assign contributions without confirming them with the team.

| Team Member | Roll Number | Parts Built | Tests / Evidence Produced |
|---|---|---|---|
| Nayna Sharma | 1262241524 | To be confirmed | To be confirmed |
| Harshad Pardhi | 1262241534 | To be confirmed | To be confirmed |
| Aaryan Kumbhare | 1262241729 | To be confirmed | To be confirmed |
| Anurag Harapanahalli | 1262241775 | To be confirmed | To be confirmed |

## Known Limitations

Refer to the Known Limitations section in `README.md`. Important points to discuss include:

- The load balancer is a single point of failure.
- The implementation is designed around three configured backends and fixed weights.
- Authentication and production-grade access control are outside the current scope.
- The benchmark is limited and does not establish a universal algorithm ranking.
- Docker Desktop's internal network traffic may not be directly visible on the host's capture interface.
- Automated tests cannot establish behavior for every possible workload or deployment environment.

## Before You Submit

Run these checks from the project root:

```bash
# Run automated tests
.venv/bin/python -m unittest discover -s tests -v

# Confirm the Python version used
.venv/bin/python --version

# Regenerate load-testing graphs if the CSV data has changed
.venv/bin/python docs/load-testing/generate_graphs.py

# Validate the Compose configuration
docker compose config --quiet

# Check service status
docker compose ps

# Check whitespace and patch errors
git diff --check

# Review outstanding changes
git status
```

Ensure the required screenshots, final test results, team contributions, and report are included before submission.
