"""Vantora monitoring dashboard.

The browser only talks to this server. The server forwards two requests to
the load balancer, so the load balancer's address is configured here, in one
place, and never hardcoded in the browser:

    GET  /api/stats      ->  <LB_URL>/stats
    POST /api/algorithm  ->  <LB_URL>/algorithm

Configuration (environment variables):

    LB_URL           Load balancer address, as seen from THIS server.
                     Local default:   http://127.0.0.1:8080
                     Docker Compose:  http://load-balancer:8080
                     (a container must use the service name; 127.0.0.1
                     inside a container is the container itself)
    DASHBOARD_HOST   Interface to listen on. Default 127.0.0.1 (this
                     computer only). Docker Compose sets 0.0.0.0.
    DASHBOARD_PORT   Port to listen on. Default 5050.
    LB_TIMEOUT       Seconds to wait for the load balancer. Default 3.
    DASHBOARD_DEBUG  Set to 1 to enable Flask's debugger and auto-reload.
                     Off by default, and ignored unless DASHBOARD_HOST is a
                     loopback address (the debugger allows remote code
                     execution and must never be exposed on a network).
"""

import os
import socket
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, render_template, request

DEFAULT_LB_URL = "http://127.0.0.1:8080"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY_BYTES = 1024


def load_config(environ=None):
    """Read the dashboard settings from environment variables."""

    environ = os.environ if environ is None else environ

    lb_url = (environ.get("LB_URL") or DEFAULT_LB_URL).strip().rstrip("/")

    if not lb_url.startswith(("http://", "https://")):
        raise ValueError(
            f"LB_URL must start with http:// or https://, got {lb_url!r}"
        )

    host = (environ.get("DASHBOARD_HOST") or "127.0.0.1").strip()

    debug_requested = (
        (environ.get("DASHBOARD_DEBUG") or "").strip().lower()
        in ("1", "true", "yes", "on")
    )

    return {
        "lb_url": lb_url,
        "host": host,
        "port": int(environ.get("DASHBOARD_PORT") or 5050),
        "timeout": float(environ.get("LB_TIMEOUT") or 3),
        "debug_requested": debug_requested,
        # The debugger is only allowed on a loopback address.
        "debug": debug_requested and host in LOOPBACK_HOSTS,
    }


def create_app(config=None):
    config = load_config() if config is None else config

    app = Flask(__name__)

    def forward(method, path, body=None):
        """Call the load balancer and return its answer to the browser."""

        headers = {}

        if body is not None:
            headers["Content-Type"] = "application/json"

        upstream = urllib.request.Request(
            config["lb_url"] + path,
            data=body,
            method=method,
            headers=headers
        )

        try:
            with urllib.request.urlopen(
                upstream, timeout=config["timeout"]
            ) as response:
                return Response(
                    response.read(),
                    status=response.status,
                    content_type="application/json"
                )

        except urllib.error.HTTPError as error:
            # The load balancer answered with an error (for example 400
            # "Invalid algorithm"): pass it on unchanged.
            return Response(
                error.read(),
                status=error.code,
                content_type="application/json"
            )

        except (urllib.error.URLError, socket.timeout, OSError) as error:
            reason = getattr(error, "reason", error)

            return jsonify({
                "error": "Cannot reach the load balancer",
                "detail": f"{config['lb_url']}: {reason}"
            }), 502

    @app.route("/")
    def dashboard():
        return render_template("index.html")

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.route("/api/stats")
    def api_stats():
        return forward("GET", "/stats")

    @app.route("/api/algorithm", methods=["POST"])
    def api_algorithm():
        body = request.get_data()

        if len(body) > MAX_BODY_BYTES:
            return jsonify({
                "success": False,
                "message": "Request body too large"
            }), 413

        return forward("POST", "/algorithm", body)

    return app


app = create_app()


if __name__ == "__main__":
    settings = load_config()

    print("======================================")
    print("     Load Balancer Dashboard")
    print("======================================")
    print(f"Listening on:  http://{settings['host']}:{settings['port']}")
    print(f"Load balancer: {settings['lb_url']}")

    if settings["debug_requested"] and not settings["debug"]:
        print(
            "WARNING: DASHBOARD_DEBUG ignored. The debugger is only "
            "enabled when DASHBOARD_HOST is a loopback address."
        )

    print("======================================")

    app.run(
        host=settings["host"],
        port=settings["port"],
        debug=settings["debug"]
    )
