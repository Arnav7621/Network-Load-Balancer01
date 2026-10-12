# Submission checklist

Evidence is marked **Complete** (in the repository and checked), **Complete,
limited** (present but with a stated gap) or **Needs your action** (you must
produce it). Nothing marked "Needs your action" exists yet.

## Artifacts to submit

| # | Artifact | Where | Status |
|---|---|---|---|
| 1 | Source code: load balancer, backend, dashboard | `lb/`, `backend/`, `dashboard/` | Complete |
| 2 | README (setup, run, Docker, endpoints, configuration, limitations) | `README.md` | Complete |
| 3 | Problem statement and objectives | Written below | Complete (copy into your report) |
| 4 | Architecture, request flow, failure handling, test strategy | `docs/architecture-and-testing.md` | Complete |
| 5 | Protocol / message design: endpoints, status codes, headers | `README.md` (Endpoints), `docs/architecture-and-testing.md` | Complete |
| 6 | Docker Compose (3 backends, load balancer, dashboard) | `docker-compose.yml`, `*/Dockerfile` | **Needs your action:** run `docker compose up --build`, confirm all five services are healthy, take a screenshot of `docker compose ps`. The dashboard service was added without being run in Docker |
| 7 | Automated tests and their result | `tests/` | Complete. Recorded: 107 tests OK on Python 3.12 (12 October 2026). **Needs your action:** run on your own computer and record the result |
| 8 | Test report for normal and failure conditions | Test names in `docs/architecture-and-testing.md`; live results from the demo | Complete, limited: write the results table from your own run (template below) |
| 9 | Performance results (throughput, response time, failures) | `docs/load-testing/` | Complete, limited: one run per algorithm, identical backends. State this in the report. The old `locust-results.txt` is superseded; do not cite it |
| 10 | Wireshark: client-to-load-balancer and load-balancer-to-backend captures | `docs/wireshark/`, `client-to-load-balancer.pcapng` | Complete, limited: genuine captures but every client request went to Backend-9001 |
| 11 | Wireshark: distribution across all backends, failover, recovery, plus screenshots | Recipes in `docs/wireshark/README.md` | **Needs your action** (5 captures and 6 screenshots, listed in that file) |
| 12 | Dashboard screenshots | – | **Needs your action** (list below) |
| 13 | Five-minute demonstration | Script below | **Needs your action:** rehearse it once |
| 14 | Individual contribution of each team member | Template below | **Needs your action** |
| 15 | Viva preparation | Notes below | Complete (read and adapt) |

### Problem statement and objectives (for your report)

Web services need to stay available and responsive when demand grows and when
servers fail. A single server is a bottleneck and a single point of failure.
Vantora is a load balancer that sits in front of three backend servers and
(1) distributes HTTP requests with a choice of algorithms, (2) detects failed
backends and stops sending them traffic, (3) keeps serving clients while a
backend is down by retrying on another one, (4) shows the system's state in
real time, and (5) is evaluated with automated tests, a load test and packet
captures.

## Evidence you still have to produce

- [ ] `docker compose ps` screenshot showing five healthy services
- [ ] Dashboard, all backends healthy
- [ ] Dashboard with one backend stopped (status "Degraded", circuit event)
- [ ] Dashboard after the algorithm is changed from the selector
- [ ] Wireshark captures A to E and screenshots (see `docs/wireshark/README.md`)
- [ ] Terminal screenshot of `python3 -m unittest discover -s tests -v` ending with `OK`
- [ ] Your own test-run result and Python version
- [ ] Team contribution table
- [ ] Optional: a `/slow`-based benchmark that can separate the algorithms (none has been run, so make no claims about it)

## Test results table (fill in from your own demonstration)

| Scenario | How to run it | Expected result | Your result |
|---|---|---|---|
| Round Robin split | `generate_traffic.py --algorithm "Round Robin" --requests 30` | 10 / 10 / 10 | |
| Weighted split | `generate_traffic.py --algorithm "Weighted Round Robin" --requests 30` | 15 / 10 / 5 | |
| Least Connections, one request at a time | `generate_traffic.py --algorithm "Least Connections" --requests 18` | 18 / 0 / 0 | |
| Least Connections, overlapping slow requests | `generate_traffic.py --algorithm "Least Connections" --path "/slow?ms=500" --concurrency 6 --requests 18` | All three used, about 6 / 6 / 6 | |
| Slow request succeeds | `curl "http://localhost:8080/slow"` | 200 after about 5 s | |
| One backend stopped | Stop `backend-9002`, send 12 requests | All 200, none from 9002, circuit OPEN | |
| Backend returns | Start it again, wait about 16 s, send 12 requests | 9002 serves again | |
| All backends stopped | Stop all three | 503 | |

(`generate_traffic.py` is `docs/wireshark/generate_traffic.py`, run with
`python3` from the project root.)

## Five-minute demonstration

Start the stack (`docker compose up --build`, or the five commands in the
README) and open the dashboard on one side of the screen.

1. **What it is (30 s).** Show the architecture diagram in the README and the
   dashboard: three healthy backends, current algorithm, verdict.
2. **Distribution (60 s).** Run the Round Robin recipe. Show the request
   counts on the dashboard rising 10 / 10 / 10. Change the algorithm from the
   selector to Weighted Round Robin and run it again: 15 / 10 / 5.
3. **Least Connections (45 s).** Run the one-at-a-time recipe (18 / 0 / 0) and
   explain that nothing overlaps. Run the overlapping slow-request recipe and
   show all backends used.
4. **Failover (90 s).** Stop Backend-9002 (`docker compose stop backend-9002`).
   Send traffic: every request still returns 200. Show the dashboard: the
   circuit opens first (after three failed requests), then the health checker
   marks the backend DOWN. Explain that the two mechanisms are independent.
5. **Recovery (30 s).** Start the backend again. After the health checks and the
   10-second cooldown it takes traffic again.
6. **Packet evidence (45 s).** Open a capture in Wireshark with the filter from
   `docs/wireshark/README.md`: one connection from the client to the load
   balancer and a separate connection from the load balancer to the backend.
7. **Results and limits (30 s).** Show the benchmark table and say what it does
   and does not prove.

## Viva notes

- **Why a reverse proxy rather than DNS round robin?** The proxy sees every
  request, so it can check backend health, retry, and count connections;
  DNS cannot.
- **Round Robin, Weighted Round Robin or Least Connections?** Round Robin for
  similar servers and similar requests; weighted when servers differ in
  capacity; Least Connections when request durations vary. Least Connections
  only helps when requests overlap, which is why one-at-a-time requests all go
  to the first backend.
- **Why both health checks and a circuit breaker?** Health checks are active
  and work without traffic but are slow (about 6 s). The circuit breaker is
  passive and reacts after three failed requests. Together they cover both.
- **Why consecutive failures, not one?** One random error should not remove a
  good server. Requiring three in a row (and two successes to recover) avoids
  flapping.
- **What is HALF_OPEN for?** After the cooldown one trial request tests the
  backend. If it fails the circuit reopens, so a recovering server is not
  flooded.
- **Why a connect timeout and a read timeout?** An unreachable backend should
  fail fast, but a slow healthy backend must be allowed to finish. One timeout
  could not do both; that was a real bug (`/slow` takes 5 s, the old timeout
  was 3 s).
- **When is a retry safe?** Always if the request never reached the backend.
  After it was sent, only for methods that can be repeated (GET, HEAD,
  OPTIONS), because the backend may already have processed it.
- **What if the load balancer fails?** It is a single point of failure. A
  second instance with a virtual IP (keepalived/VRRP) or DNS failover would
  fix that; it is outside this project.
- **Layer 4 or Layer 7?** Layer 7: it understands HTTP (status codes, paths,
  headers such as `X-Served-By`).
- **What are the limits of the benchmark?** One run per algorithm, identical
  backends, one computer. It cannot rank the algorithms.
- **What does Wireshark show?** Two separate TCP connections (client to load
  balancer, load balancer to backend), the added `X-Served-By` header, health
  probes every 2 s, and a refused connection (TCP RST) when a backend is
  stopped.

## Contribution table (template)

| Team member | Roll number | Parts built | Tests / evidence produced |
|---|---|---|---|
| | | | |
| | | | |
| | | | |

## Known limitations to state in the report

See "Known limitations" in `README.md`. The ones examiners are most likely to
ask about: only GET is proxied; no authentication; a single load balancer;
exactly three backends with fixed weights; the benchmark's scope; and that
Docker's internal network cannot be captured from the host.

## Before you submit

```bash
python3 -m unittest discover -s tests -v          # record the result
python3 docs/load-testing/generate_graphs.py      # graphs and table match the CSV files
docker compose config --quiet && docker compose up --build   # check all five services become healthy
git status                                        # nothing unintended is uncommitted
```
