"""Tests for the dashboard server (dashboard/app.py).

A small fake load balancer runs on a free port, so these tests check what
the dashboard really sends and returns, without needing Vantora to be
running. They are skipped if Flask is not installed.
"""

import importlib.util
import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD_DIR = os.path.join(ROOT, "dashboard")

HAS_FLASK = importlib.util.find_spec("flask") is not None


def load_dashboard_module():
    """Import dashboard/app.py under its own name (backend/app.py is also 'app')."""
    name = "vantora_dashboard_app"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(DASHBOARD_DIR, "app.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                   # Flask needs this to find templates
    spec.loader.exec_module(module)
    return module


class FakeLoadBalancer:
    """Answers /stats and /algorithm like the real load balancer."""

    def __init__(self):
        self.requests = []                       # (method, path, body, content_type)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                owner.requests.append(("GET", self.path, b"", None))

                if self.path == "/stats":
                    self._send(200, {"algorithm": "Round Robin", "backends": []})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                owner.requests.append(
                    ("POST", self.path, body, self.headers.get("Content-Type"))
                )

                requested = json.loads(body).get("algorithm")

                if requested in ("Round Robin", "Least Connections"):
                    self._send(200, {"success": True, "algorithm": requested})
                else:
                    self._send(400, {"success": False, "message": "Invalid algorithm"})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def free_closed_port():
    """A local port with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(HAS_FLASK, "Flask is not installed")
class DashboardConfigTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.module = load_dashboard_module()

    def test_defaults_are_local_only_and_not_debug(self):
        config = self.module.load_config({})

        self.assertEqual(config["lb_url"], "http://127.0.0.1:8080")
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], 5050)
        self.assertFalse(config["debug"])

    def test_docker_style_settings(self):
        config = self.module.load_config({
            "LB_URL": "http://load-balancer:8080/",
            "DASHBOARD_HOST": "0.0.0.0",
        })

        self.assertEqual(config["lb_url"], "http://load-balancer:8080")
        self.assertEqual(config["host"], "0.0.0.0")

    def test_debug_is_honoured_only_on_a_loopback_address(self):
        local = self.module.load_config({"DASHBOARD_DEBUG": "1"})
        self.assertTrue(local["debug"])

        exposed = self.module.load_config({
            "DASHBOARD_DEBUG": "1",
            "DASHBOARD_HOST": "0.0.0.0",
        })
        self.assertTrue(exposed["debug_requested"])
        self.assertFalse(exposed["debug"])          # never exposed on a network

    def test_invalid_load_balancer_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.load_config({"LB_URL": "load-balancer:8080"})

    def test_debug_mode_is_not_hardcoded_in_the_source(self):
        with open(os.path.join(DASHBOARD_DIR, "app.py")) as source:
            text = source.read()

        self.assertNotIn("debug=True", text)


@unittest.skipUnless(HAS_FLASK, "Flask is not installed")
class DashboardProxyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.module = load_dashboard_module()

    def setUp(self):
        self.lb = FakeLoadBalancer()
        self.addCleanup(self.lb.stop)

        self.client = self.module.create_app(
            self.module.load_config({"LB_URL": self.lb.url})
        ).test_client()

    def test_stats_are_forwarded_from_the_load_balancer(self):
        response = self.client.get("/api/stats")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(), {"algorithm": "Round Robin", "backends": []}
        )
        self.assertEqual(self.lb.requests[0][:2], ("GET", "/stats"))

    def test_algorithm_change_is_forwarded_with_its_json_body(self):
        response = self.client.post(
            "/api/algorithm", json={"algorithm": "Least Connections"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        method, path, body, content_type = self.lb.requests[0]
        self.assertEqual((method, path), ("POST", "/algorithm"))
        self.assertEqual(json.loads(body), {"algorithm": "Least Connections"})
        self.assertEqual(content_type, "application/json")

    def test_error_status_from_the_load_balancer_is_passed_through(self):
        response = self.client.post("/api/algorithm", json={"algorithm": "Nope"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["message"], "Invalid algorithm")

    def test_unreachable_load_balancer_gives_a_clear_502(self):
        dead_url = f"http://127.0.0.1:{free_closed_port()}"
        client = self.module.create_app(
            self.module.load_config({"LB_URL": dead_url})
        ).test_client()

        for response in (
            client.get("/api/stats"),
            client.post("/api/algorithm", json={"algorithm": "Round Robin"}),
        ):
            self.assertEqual(response.status_code, 502)

            body = response.get_json()
            self.assertEqual(body["error"], "Cannot reach the load balancer")
            self.assertIn(dead_url, body["detail"])    # says WHERE it tried

    def test_oversized_algorithm_request_is_rejected_without_forwarding(self):
        response = self.client.post(
            "/api/algorithm", data=b"x" * 2000, content_type="application/json"
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.lb.requests, [])

    def test_health_endpoint_does_not_need_the_load_balancer(self):
        self.lb.stop()                                  # load balancer is gone

        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_page_and_script_do_not_hardcode_the_load_balancer_address(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertFalse(
            b"127.0.0.1:8080" in page.data,
            "index.html hardcodes the load balancer address"
        )

        with open(os.path.join(DASHBOARD_DIR, "static", "js", "app.js")) as js:
            script = js.read()

        self.assertFalse(
            "127.0.0.1:8080" in script,
            "app.js hardcodes the load balancer address"
        )
        self.assertTrue('"/api/stats"' in script, "app.js must call /api/stats")
        self.assertTrue(
            '"/api/algorithm"' in script, "app.js must call /api/algorithm"
        )


if __name__ == "__main__":
    unittest.main()
