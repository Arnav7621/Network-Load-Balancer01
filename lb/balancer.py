
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import http.client
import socket
import threading
import time
import json
import os

from algorithms import (
    Backend,
    RoundRobin,
    LeastConnections,
    WeightedRoundRobin
)


# ==================================================
# Configuration
# ==================================================

LB_HOST = "0.0.0.0"
LB_PORT = 8080


# ==================================================
# Timeouts and retry policy
# ==================================================
#
# CONNECT_TIMEOUT   How long to wait for a TCP connection to a backend.
#                   Short, so an unreachable backend fails over quickly.
# READ_TIMEOUT      How long to wait for the backend's response once the
#                   request has been sent. Must be longer than the slowest
#                   legitimate request (the backend's /slow endpoint
#                   defaults to 5 seconds).
# REQUEST_DEADLINE  Total time budget for one client request, across all
#                   failover attempts.
#
# All three can be changed with environment variables (in seconds).

def read_seconds(name, default):
    """Read a positive number of seconds from an environment variable."""

    raw = os.getenv(name)

    if raw is None or raw.strip() == "":
        return default

    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be a number of seconds, got {raw!r}"
        )

    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return value


DEFAULT_CONNECT_TIMEOUT = 1.0
DEFAULT_READ_TIMEOUT = 10.0
DEFAULT_REQUEST_DEADLINE = 20.0

CONNECT_TIMEOUT = read_seconds(
    "VANTORA_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT
)
READ_TIMEOUT = read_seconds(
    "VANTORA_READ_TIMEOUT", DEFAULT_READ_TIMEOUT
)
REQUEST_DEADLINE = read_seconds(
    "VANTORA_REQUEST_DEADLINE", DEFAULT_REQUEST_DEADLINE
)

# Do not start a new attempt with less time than this left in the budget.
MIN_ATTEMPT_SECONDS = 0.05

# Methods that may be repeated on another backend even if the first backend
# might already have processed the request. (The load balancer currently
# proxies GET only; the policy is written so that it stays correct if other
# methods are added.)
RETRY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_retry_safe(method, request_sent):
    """May a failed attempt be repeated on another backend?

    * The request never reached the backend (connection refused, connect
      timeout): nothing was processed, so retrying is always safe.
    * The request was sent: the backend may have processed it, so it may
      only be repeated if the method is safe to repeat.
    """

    return (not request_sent) or method.upper() in RETRY_SAFE_METHODS


class BackendError(Exception):
    """A failed attempt to get a response from one backend."""

    def __init__(self, kind, request_sent, original):
        super().__init__(str(original))
        self.kind = kind                    # "timeout" or "unreachable"
        self.request_sent = request_sent    # may the backend have seen it?


# ==================================================
# Backend Servers
# ==================================================

# Local mode:
#   127.0.0.1,127.0.0.1,127.0.0.1
#
# Docker mode:
#   backend-9001,backend-9002,backend-9003

backend_hosts = os.getenv(
    "VANTORA_BACKEND_HOSTS",
    "127.0.0.1,127.0.0.1,127.0.0.1"
).split(",")


if len(backend_hosts) != 3:
    raise ValueError(
        "VANTORA_BACKEND_HOSTS must contain exactly three hosts"
    )


backends = [
    Backend(backend_hosts[0].strip(), 9001, weight=3),
    Backend(backend_hosts[1].strip(), 9002, weight=2),
    Backend(backend_hosts[2].strip(), 9003, weight=1)
]


# ==================================================
# Load Balancing Algorithms
# ==================================================

algorithms = {
    "Round Robin": RoundRobin(),
    "Least Connections": LeastConnections(),
    "Weighted Round Robin": WeightedRoundRobin()
}

algorithm_name = "Least Connections"
algorithm = algorithms[algorithm_name]
algorithm_lock = threading.Lock()


# ==================================================
# Health Monitor
# ==================================================

def health_check():
    """Continuously monitor backend health."""

    while True:
        for backend in backends:
            connection = None
            state_change = None

            try:
                connection = http.client.HTTPConnection(
                    backend.host,
                    backend.port,
                    timeout=2
                )

                connection.request("GET", "/health")
                response = connection.getresponse()
                response.read()

                if response.status == 200:
                    state_change = backend.record_health_success()
                else:
                    state_change = backend.record_health_failure()

            except Exception as error:
                state_change = backend.record_health_failure()

                print(
                    f"Health check failed: "
                    f"Backend-{backend.port}: {error}"
                )

            finally:
                if connection is not None:
                    connection.close()

            if state_change == "DOWN":
                print(f"Backend-{backend.port} is DOWN")

            elif state_change == "UP":
                print(f"Backend-{backend.port} is UP")

        time.sleep(2)


# ==================================================
# HTTP Request Handler
# ==================================================

class LoadBalancerHandler(BaseHTTPRequestHandler):

    # ==================================================
    # Send JSON Response
    # ==================================================

    def send_json(self, data, status=200):
        response = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    # ==================================================
    # OPTIONS Requests
    # ==================================================

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )
        self.end_headers()

    # ==================================================
    # GET Requests
    # ==================================================

    def do_GET(self):

        # Statistics endpoint
        if self.path == "/stats":
            stats = []

            for backend in backends:
                stats.append({
                    "server": f"Backend-{backend.port}",
                    "host": backend.host,
                    "port": backend.port,
                    "healthy": backend.healthy,
                    "status": "UP" if backend.healthy else "DOWN",
                    "active_connections": backend.active_conns,
                    "total_requests": backend.total_requests,
                    "errors": backend.errors,
                    "avg_response_time": round(
                        backend.avg_response_time, 4
                    ),
                    "circuit": backend.breaker.snapshot()
                })

            with algorithm_lock:
                current_algorithm = algorithm_name

            self.send_json({
                "algorithm": current_algorithm,
                "backends": stats
            })
            return

        # Protect the algorithm endpoint from GET requests
        if self.path.startswith("/algorithm"):
            self.send_json({
                "success": False,
                "message": "Endpoint not found"
            }, status=404)
            return

        self.proxy_request("GET")

    # ==================================================
    # Reverse proxy with failover
    # ==================================================

    def proxy_request(self, method):
        """Forward the request to a backend, failing over when needed.

        Only the call to the backend is retried. As soon as a backend has
        answered (even with a 5xx) its response is returned to the client
        and the request is never repeated, and a failure while writing to
        the client is never treated as a backend failure.
        """

        deadline = time.monotonic() + REQUEST_DEADLINE
        attempted_backends = []
        last_failure = None

        for attempt in range(len(backends)):

            if deadline - time.monotonic() < MIN_ATTEMPT_SECONDS:
                last_failure = "timeout"
                break

            with algorithm_lock:
                selected_algorithm = algorithm

            available_backends = [
                backend for backend in backends
                if (
                    backend.routable()
                    and backend not in attempted_backends
                )
            ]

            if not available_backends:
                break

            backend = selected_algorithm.pick(available_backends)

            if backend is None:
                break

            attempted_backends.append(backend)

            # Circuit breaker permission
            if not backend.breaker.acquire():
                print(
                    f"Circuit breaker blocked "
                    f"Backend-{backend.port}"
                )
                continue

            result = self.call_backend(backend, method, deadline)

            if isinstance(result, BackendError):
                last_failure = result.kind

                if not is_retry_safe(method, result.request_sent):
                    self.send_json({
                        "error": (
                            "Backend failed after receiving the request. "
                            "The request was not repeated because it may "
                            "already have been processed"
                        )
                    }, status=502)
                    return

                print(
                    f"Failover attempt "
                    f"{attempt + 1}/{len(backends)}"
                )
                continue

            self.send_backend_response(backend, *result)
            return

        # No backend could answer
        if last_failure == "timeout":
            self.send_json({
                "error": "Backend response timed out"
            }, status=504)
            return

        self.send_json({
            "error": "All backend servers are unavailable"
        }, status=503)

    def call_backend(self, backend, method, deadline):
        """Ask one backend for the response.

        Returns (status, headers, body) when the backend answered, or a
        BackendError when it could not. Updates the backend's statistics
        and circuit breaker.
        """

        connection = None
        request_sent = False
        start_time = time.time()
        backend.active_conns += 1

        try:
            connection = http.client.HTTPConnection(
                backend.host,
                backend.port,
                timeout=min(
                    CONNECT_TIMEOUT,
                    max(MIN_ATTEMPT_SECONDS, deadline - time.monotonic())
                )
            )
            connection.connect()

            # Connected: switch from the short connect timeout to the
            # read timeout, limited by the remaining request budget.
            connection.sock.settimeout(
                max(
                    MIN_ATTEMPT_SECONDS,
                    min(READ_TIMEOUT, deadline - time.monotonic())
                )
            )

            # From here on the backend may have processed the request.
            request_sent = True
            connection.request(method, self.path)

            response = connection.getresponse()
            response_body = response.read()

        except Exception as error:
            if isinstance(error, (socket.timeout, TimeoutError)):
                kind = "timeout"
            else:
                kind = "unreachable"

            backend.breaker.record_failure()
            backend.errors += 1

            print(
                f"Request failed on Backend-{backend.port} "
                f"({kind}): {error}"
            )

            return BackendError(kind, request_sent, error)

        finally:
            backend.active_conns = max(0, backend.active_conns - 1)

            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        # The backend answered: record the outcome.
        if response.status >= 500:
            backend.errors += 1
            backend.breaker.record_failure()
        else:
            backend.breaker.record_success()

        elapsed = time.time() - start_time
        backend.total_requests += 1
        backend.total_response_time += elapsed
        backend.avg_response_time = (
            backend.total_response_time / backend.total_requests
        )

        return response.status, response.getheaders(), response_body

    def send_backend_response(self, backend, status, headers, body):
        """Return a backend's response to the client (never retried)."""

        try:
            self.send_response(status)

            for header, value in headers:
                if header.lower() not in (
                    "connection",
                    "transfer-encoding",
                    "keep-alive",
                    "proxy-authenticate",
                    "proxy-authorization",
                    "te",
                    "trailers",
                    "upgrade",
                    "content-length"
                ):
                    self.send_header(header, value)

            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Served-By", f"Backend-{backend.port}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            print(
                f"Client response write failed after "
                f"Backend-{backend.port} responded: {error}"
            )

    # ==================================================
    # POST Requests
    # ==================================================

    def do_POST(self):

        # Change routing algorithm
        if self.path == "/algorithm":
            try:
                content_length = int(
                    self.headers.get("Content-Length", 0)
                )

                if content_length <= 0:
                    self.send_json({
                        "success": False,
                        "message": "Request body is required"
                    }, status=400)
                    return

                body = self.rfile.read(content_length)
                data = json.loads(body.decode("utf-8"))

                if not isinstance(data, dict):
                    self.send_json({
                        "success": False,
                        "message": "JSON object expected"
                    }, status=400)
                    return

                requested_algorithm = data.get("algorithm")

                if requested_algorithm not in algorithms:
                    self.send_json({
                        "success": False,
                        "message": "Invalid algorithm",
                        "available_algorithms": list(algorithms.keys())
                    }, status=400)
                    return

                global algorithm
                global algorithm_name

                with algorithm_lock:
                    algorithm_name = requested_algorithm
                    algorithm = algorithms[requested_algorithm]

                print(
                    f"Routing algorithm changed to {algorithm_name}"
                )

                self.send_json({
                    "success": True,
                    "algorithm": algorithm_name,
                    "message": (
                        f"Routing algorithm changed to "
                        f"{algorithm_name}"
                    )
                })
                return

            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                print(f"Invalid algorithm request: {error}")

                self.send_json({
                    "success": False,
                    "message": "Invalid request"
                }, status=400)
                return

            except Exception as error:
                print(f"Algorithm change failed: {error}")

                self.send_json({
                    "success": False,
                    "message": "Internal server error"
                }, status=500)
                return

        # Unknown POST endpoint
        self.send_json({
            "success": False,
            "message": "Endpoint not found"
        }, status=404)


# ==================================================
# Start Server
# ==================================================

if __name__ == "__main__":
    server = ThreadingHTTPServer(
        (LB_HOST, LB_PORT),
        LoadBalancerHandler
    )

    health_thread = threading.Thread(
        target=health_check,
        daemon=True
    )
    health_thread.start()

    print("Health Monitor: ACTIVE")
    print("====================================")
    print("     Dynamic Load Balancer")
    print("====================================")
    print(f"Listening on {LB_HOST}:{LB_PORT}")
    print(f"Algorithm: {algorithm_name}")
    print(
        "Circuit breaker: ACTIVE "
        "(3 failures -> OPEN, 10s cooldown)"
    )
    print("Backends:")

    for backend in backends:
        print(
            f"  - Backend-{backend.port}: "
            f"{backend.host}:{backend.port}"
        )

    print("====================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Load Balancer...")
    finally:
        server.server_close()
