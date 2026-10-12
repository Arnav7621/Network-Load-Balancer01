# Vantora: Dynamic Load Balancer and Health Monitor

Vantora is an HTTP reverse-proxy load balancer written in Python, with three
backend servers and a live monitoring dashboard. It distributes requests with
a choice of routing algorithms, watches the health of every backend, keeps
serving clients when a backend fails, and shows what is happening in a web
dashboard.

This project was built for the Computer Networks Laboratory (CSE20110) mini
project.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Ports](#ports)
- [Prerequisites](#prerequisites)
- [Quick start with Docker](#quick-start-with-docker)
- [Run without Docker](#run-without-docker)
- [Using the load balancer](#using-the-load-balancer)
- [Endpoints](#endpoints)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [The dashboard](#the-dashboard)
- [Tests](#tests)
- [Load testing](#load-testing)
- [Wireshark evidence](#wireshark-evidence)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [More documentation](#more-documentation)

## Features

- **Three routing algorithms:** Round Robin, Weighted Round Robin and Least
  Connections, switchable while the system is running.
- **Health monitoring:** a background thread checks every backend every two
  seconds. Three failed checks in a row mark a backend DOWN; two successful
  checks in a row bring it back UP.
- **Retry and failover:** if a backend cannot answer, the request is repeated
  on another healthy backend, and only when repeating it is safe.
- **Circuit breaker per backend:** after three consecutive failed requests a
  backend is taken out of rotation for ten seconds, then tested with a single
  trial request (CLOSED, OPEN and HALF_OPEN states).
- **Timeouts that allow slow requests:** a short connect timeout for fast
  failover and a longer read timeout so slow but healthy responses succeed.
- **Statistics API:** requests, active connections, errors, average response
  time, health and circuit state for every backend.
- **Monitoring dashboard:** status verdict, topology, per-backend table,
  traffic and latency charts, health and circuit events, and an algorithm
  selector.
- **Docker Compose:** one command starts the backends, the load balancer and
  the dashboard.
- **Automated tests, a load-test report and packet-capture tools.**

## Architecture

```text
                       +-----------------------------+
   Browser  ---------> |  Dashboard (Flask)  :5050   |
                       +--------------+--------------+
                                      | /api/stats, /api/algorithm
                                      v
   Client  --HTTP-->   +-----------------------------+
   (curl, Locust)      |  Load balancer      :8080   |
                       |   - routing algorithms      |
                       |   - health checker thread   |
                       |   - circuit breaker/backend |
                       |   - retry and failover      |
                       +-----+-------+-------+-------+
                             |       |       |
                             v       v       v
                        Backend   Backend   Backend
                         :9001     :9002     :9003
                       (Flask)    (Flask)    (Flask)
```

The dashboard is a separate server. The browser talks only to the dashboard,
and the dashboard forwards two requests to the load balancer (`GET /stats` and
`POST /algorithm`). A request through the load balancer goes: client, load
balancer, one backend, and back. The load balancer opens its own connection to
the backend, adds an `X-Served-By` header to the answer, and returns it.

See [docs/architecture-and-testing.md](docs/architecture-and-testing.md) for
the request lifecycle, the failure-handling rules and the test strategy.

## Ports

| Component | Port | Notes |
|---|---|---|
| Load balancer | 8080 | Clients send requests here |
| Dashboard | 5050 | Open in a browser |
| Backend 1, 2, 3 | 9001, 9002, 9003 | Also published by Docker Compose for testing |

## Prerequisites

- **Docker Desktop** (for the Docker route), or
- **Python 3.9 or newer** (for running without Docker; the project was
  developed on Python 3.9.6 and the Docker images use Python 3.11)
- Git
- A web browser. The dashboard charts load Chart.js from a CDN, so they need
  internet access (the rest of the dashboard works without it).
- Optional: Wireshark (packet capture) and Locust (load testing).

## Quick start with Docker

From the project root:

```bash
docker compose up --build
```

Then open:

- Dashboard: <http://localhost:5050>
- Statistics API: <http://localhost:8080/stats>
- A proxied request: <http://localhost:8080/>

Useful commands:

```bash
docker compose ps          # all five services should become "healthy"
docker compose logs -f     # live logs
docker compose down        # stop and remove the containers
```

Inside Compose the services find each other by service name
(`backend-9001`, `load-balancer`, ...). The load balancer is given its
backends through `VANTORA_BACKEND_HOSTS`, and the dashboard is given the load
balancer through `LB_URL`; both are set in `docker-compose.yml`.

To demonstrate failover with Docker:

```bash
docker compose stop backend-9002      # take a backend away
docker compose start backend-9002     # bring it back
```

## Run without Docker

Create a virtual environment and install the dependencies (once):

```bash
python3 -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
```

(`pip install Flask==3.1.3` is enough to run the application; the other
packages are for tests, graphs, captures and load testing.)

Start each component in its own terminal, from the project root, with the
virtual environment active:

```bash
python backend/app.py 9001
python backend/app.py 9002
python backend/app.py 9003
python lb/balancer.py
python dashboard/app.py
```

Without any settings, the load balancer talks to the backends on
`127.0.0.1`, and the dashboard talks to the load balancer at
`http://127.0.0.1:8080`. The dashboard listens on this computer only.

## Using the load balancer

```bash
# A proxied request. The X-Served-By header says which backend answered.
curl -i http://localhost:8080/

# Statistics (JSON)
curl http://localhost:8080/stats

# Switch the routing algorithm
curl -X POST http://localhost:8080/algorithm \
     -H "Content-Type: application/json" \
     -d '{"algorithm": "Round Robin"}'

# A slow request (the backend waits 1000 ms before answering)
curl "http://localhost:8080/slow?ms=1000"
```

The algorithm names are exactly `Round Robin`, `Weighted Round Robin` and
`Least Connections`. The load balancer starts with `Least Connections`.

## Endpoints

**Load balancer (port 8080)**

| Method and path | Purpose |
|---|---|
| `GET /stats` | Algorithm and per-backend statistics (JSON) |
| `POST /algorithm` | Change the algorithm; body `{"algorithm": "<name>"}`. Returns 400 for an unknown name |
| `GET <anything else>` | Proxied to a backend |

Other methods are not proxied (see [Known limitations](#known-limitations)).
The load balancer answers with **503** when no backend is available, **504**
when the backends timed out, and **502** when a request that is not safe to
repeat failed after it had been sent.

**Backend (ports 9001 to 9003)**

| Path | Purpose |
|---|---|
| `GET /` | Identifies the server (name and port) |
| `GET /health` | Health check, always answers 200 |
| `GET /slow` | Waits 5 seconds by default; `?ms=<0 to 30000>` sets the delay |

**Dashboard (port 5050)**

| Path | Purpose |
|---|---|
| `GET /` | The dashboard page |
| `GET /api/stats` | Forwards to the load balancer's `/stats` |
| `POST /api/algorithm` | Forwards to the load balancer's `/algorithm` |
| `GET /healthz` | Dashboard health (does not need the load balancer) |

If the load balancer cannot be reached, the dashboard's API answers with
**502** and a message naming the address it tried.

## How it works

**Routing algorithms**

- *Round Robin*: backends take turns.
- *Weighted Round Robin*: backends take turns in proportion to their weights.
  The weights are 3, 2 and 1 for ports 9001, 9002 and 9003 (set in
  `lb/balancer.py`), so 30 requests split 15 / 10 / 5.
- *Least Connections*: the backend with the fewest active connections wins.
  Ties go to the first backend, so requests that arrive one at a time all go
  to Backend-9001. Traffic spreads only when requests overlap.

Only backends that are healthy **and** whose circuit allows traffic are
candidates.

**Health monitoring.** Every 2 seconds the load balancer requests
`GET /health` from each backend (2 second timeout). Three consecutive failures
mark it DOWN; two consecutive successes mark it UP again.

**Circuit breaker.** Separate from health checks, it reacts to real client
requests. Three consecutive failed requests (connection errors, timeouts, or
HTTP 5xx answers) open the circuit. After 10 seconds one trial request is let
through (HALF_OPEN): success closes the circuit, failure reopens it.

**Timeouts, retry and failover.**

| Setting | Default | Meaning |
|---|---|---|
| Connect timeout | 1 s | Time allowed to open a connection to a backend |
| Read timeout | 10 s | Time allowed for the backend's response (longer than `/slow`'s 5 s) |
| Request deadline | 20 s | Total time for one client request across all attempts |

A failed attempt is retried on another backend only when that is safe: always
if the request never reached the backend, and after it was sent only for
methods that are safe to repeat (GET, HEAD, OPTIONS). Once any backend has
answered, even with an error, the answer is returned and the request is not
repeated.

Note that a backend that accepts connections but never answers (a hung
process) costs one full read timeout for each request that lands on it, until
its circuit opens after three failures. A stopped backend is different: the
connection is refused at once and failover is immediate. For demonstrations of
a hung backend you can shorten the wait, for example
`VANTORA_READ_TIMEOUT=2 python lb/balancer.py`.

## Configuration

Environment variables (all optional):

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `VANTORA_BACKEND_HOSTS` | load balancer | `127.0.0.1,127.0.0.1,127.0.0.1` | Exactly three hosts, for ports 9001, 9002, 9003 |
| `VANTORA_CONNECT_TIMEOUT` | load balancer | `1.0` | Seconds |
| `VANTORA_READ_TIMEOUT` | load balancer | `10.0` | Seconds |
| `VANTORA_REQUEST_DEADLINE` | load balancer | `20.0` | Seconds |
| `LB_URL` | dashboard | `http://127.0.0.1:8080` | Where the dashboard server finds the load balancer. In Docker use the service name: `http://load-balancer:8080` |
| `LB_TIMEOUT` | dashboard | `3` | Seconds to wait for the load balancer |
| `DASHBOARD_HOST` | dashboard | `127.0.0.1` | Interface to listen on (`0.0.0.0` in Docker) |
| `DASHBOARD_PORT` | dashboard | `5050` | Port |
| `DASHBOARD_DEBUG` | dashboard | off | `1` enables Flask's debugger, and only when the host is a loopback address |

Fixed in the source code (edit the file to change them): the ports,
the weights (`lb/balancer.py`), the health-check interval and thresholds
(`lb/balancer.py`, `lb/algorithms.py`) and the circuit-breaker settings
(`lb/algorithms.py`).

## The dashboard

Open <http://localhost:5050>. It shows an overall status message, request,
connection, error and latency figures, the load balancer and the three
backends with live traffic, a table with each backend's status, circuit state,
requests, errors and latency, traffic and latency charts, and a list of recent
events (a backend going DOWN or recovering, circuit changes, algorithm
changes). The algorithm can be changed from the selector at the top.

It refreshes every 2 seconds. If something is wrong it says what: "Load
balancer unavailable" with the address that could not be reached, or
"Dashboard server unreachable" if the dashboard server itself has stopped.

## Tests

Run the whole suite from the project root:

```bash
python3 -m unittest discover -s tests -v
```

The test count and last recorded result are in
[docs/architecture-and-testing.md](docs/architecture-and-testing.md). The tests
need no running services and no Docker; they start their own servers on free
ports. A few groups are skipped automatically if an optional package is
missing (`Flask`, `PyYAML`, `scapy`); `pip install -r requirements-dev.txt`
installs them all.

What is covered: the circuit breaker (with a fake clock), the routing
algorithms, the health-state rules, failover, slow and hung backends, mixed
fast/slow workloads, the dashboard's API, the backend endpoints, the
benchmark report against its raw data, the Compose file's consistency, and the
Wireshark tools. What is not covered, and why, is listed in the
architecture and testing document.

## Load testing

The recorded benchmark used Locust 2.34.0 with 20 users for 60 seconds per
algorithm. The results, graphs and raw CSV files are in
[docs/load-testing/](docs/load-testing/); start with
[benchmark-comparison.md](docs/load-testing/benchmark-comparison.md). The
report's table is checked against the CSV files by an automated test.

To run a benchmark yourself (Vantora must be running):

```bash
locust -f locustfile_benchmark.py --headless -u 20 -r 5 -t 60s \
       --host http://127.0.0.1:8080 --csv docs/load-testing/benchmark-runs/my-run
```

To regenerate the graphs and the results table from the CSV files:

```bash
python3 docs/load-testing/generate_graphs.py
```

Read the benchmark as an observation, not a ranking: it is one run per
algorithm, against identical backends, on one computer.

## Wireshark evidence

Packet captures and a screenshot are in [docs/wireshark/](docs/wireshark/).
The existing captures show the reverse-proxy behaviour but every client
request in them went to Backend-9001, so they do not show distribution.
[docs/wireshark/README.md](docs/wireshark/README.md) explains this, gives a
repeatable procedure for capturing distribution across all three backends,
failover and recovery, and lists the Wireshark filters and the numbers to
expect.

## Project layout

```text
backend/            Backend server (Flask), Dockerfile
lb/                 Load balancer: balancer.py, algorithms.py,
                    circuit_breaker.py, Dockerfile
dashboard/          Dashboard server (app.py), templates/, static/, Dockerfile
tests/              Automated tests and shared fixtures (helpers.py)
docs/
  architecture-and-testing.md   Design and test strategy
  submission-checklist.md       What to submit, demo script, viva notes
  load-testing/                 Benchmark report, graphs, raw CSV files
  wireshark/                    Captures, screenshot, capture tools and guide
docker-compose.yml  Starts backends, load balancer and dashboard
locustfile_benchmark.py   Locust workload used for the benchmark
locustfile.py       Earlier, lighter Locust workload (with think time)
client-to-load-balancer.pcapng   Existing client-side capture
requirements-dev.txt   All Python packages used by the project
```

## Known limitations

- **Only GET is proxied.** `HEAD` and other methods get a 501, and a `POST` to
  any path other than `/algorithm` gets a 404. Request headers and bodies are
  not forwarded to the backends.
- **No authentication.** Anyone who can reach port 8080 or the dashboard can
  change the routing algorithm. By default the load balancer listens on all
  network interfaces. This is acceptable for a lab, not for deployment.
- **Single load balancer.** If it stops, everything stops. State (statistics,
  circuit state) is in memory and resets on restart.
- **Events are built in the browser.** The dashboard notices changes by
  comparing successive polls (every 2 seconds), so the event list is empty
  after a page refresh and a change shorter than one poll is not seen. The
  "LIVE" label is a status indicator; there is no pause button.
- **Exactly three backends on fixed ports.** The weights, ports and breaker
  settings are fixed in the source, and the dashboard's topology and charts
  show ports 9001 to 9003.
- **Least Connections ties go to the first backend**, and Weighted Round
  Robin sends its share in bursts (3, then 2, then 1).
- **A hung backend is slow to detect** (one read timeout per request until the
  circuit opens), see [How it works](#how-it-works).
- **The circuit state changes lazily.** An OPEN circuit whose cooldown has
  ended is still reported as OPEN (with 0 seconds left) until the next request
  arrives.
- **Statistics counters are not locked.** Concurrent updates could in theory
  lose a count; none was observed in the tests, which compare client-visible
  results with the recorded statistics.
- **Development servers.** The backends and the dashboard run on Flask's
  built-in server, which is not a production server.
- **Benchmark scope.** One run per algorithm with 20 users, identical
  backends and a trivial `GET /`, all on one computer, with the backends run
  by Docker Compose. It cannot rank the algorithms. `locust-results.txt` is an
  earlier, shorter test that is kept only for the record.
- **Captures and Docker.** Docker Desktop's internal network is not visible to
  Wireshark on the host, so packet-capture sessions use the non-Docker setup.
- **The dashboard container** was added to Docker Compose after the rest of
  the stack was tested in Docker; run `docker compose up --build` and check
  `docker compose ps` on your own computer.
- **Python versions.** The automated tests were last run on Python 3.12, not
  on the 3.9.6 the project was developed with. The code uses only syntax that
  is valid on 3.9, but run the tests on your own environment before
  submitting.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Address already in use` | Something already uses the port. Stop the old process, or `docker compose down` if the stack is running in Docker. |
| The load balancer returns 503 straight after starting | Backends are not running, or `VANTORA_BACKEND_HOSTS` names hosts that do not resolve. Local runs need no setting; Docker sets it in `docker-compose.yml`. |
| `Name or service not known` for `backend-9001` outside Docker | `VANTORA_BACKEND_HOSTS` is set to the Docker names in your shell. Unset it. |
| Dashboard says "Load balancer unavailable" | The dashboard server cannot reach `LB_URL`. The message includes the address it tried; start the load balancer or correct `LB_URL`. |
| Dashboard says "Dashboard server unreachable" | The dashboard process has stopped; restart it. |
| Charts are empty | Chart.js is loaded from the internet; check the connection. |
| A request takes 10 seconds then succeeds | A backend is hung (accepts connections but does not answer). See [How it works](#how-it-works). |
| Wireshark shows nothing on the loopback interface | Capture on `lo0` (macOS) or `lo` (Linux), not Wi-Fi; make sure you are running Vantora without Docker. |

## More documentation

- [docs/architecture-and-testing.md](docs/architecture-and-testing.md): design, failure handling, test strategy and results
- [docs/submission-checklist.md](docs/submission-checklist.md): artifacts to submit, 5-minute demo script, viva notes
- [docs/load-testing/README.md](docs/load-testing/README.md): benchmark files and method
- [docs/wireshark/README.md](docs/wireshark/README.md): packet-capture evidence and procedure
