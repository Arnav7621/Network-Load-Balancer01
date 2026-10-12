"""Tests for the backend server's endpoints (backend/app.py).

The delay in /slow is checked by recording the value passed to sleep(), so
no test actually waits. Skipped if Flask is not installed.
"""

import importlib.util
import os
import sys
import unittest
from unittest import mock

BACKEND_APP = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "backend", "app.py"
)

HAS_FLASK = importlib.util.find_spec("flask") is not None


def load_backend_module(port="9999"):
    """Import backend/app.py, which reads the port from the command line."""
    name = "vantora_backend_app"
    spec = importlib.util.spec_from_file_location(name, BACKEND_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module

    with mock.patch.object(sys, "argv", ["app.py", port]):
        spec.loader.exec_module(module)

    return module


@unittest.skipUnless(HAS_FLASK, "Flask is not installed")
class BackendEndpointTests(unittest.TestCase):

    def setUp(self):
        self.module = load_backend_module("9999")
        self.client = self.module.app.test_client()

        # Replace the module's `time` so /slow records its delay, not waits.
        patcher = mock.patch.object(self.module, "time")
        self.fake_time = patcher.start()
        self.addCleanup(patcher.stop)

    def test_slow_defaults_to_five_seconds(self):
        response = self.client.get("/slow")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delay_ms"], 5000)
        self.fake_time.sleep.assert_called_once_with(5.0)

    def test_slow_delay_can_be_set_with_ms(self):
        response = self.client.get("/slow?ms=250")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delay_ms"], 250)
        self.fake_time.sleep.assert_called_once_with(0.25)

    def test_slow_delay_is_limited(self):
        self.client.get("/slow?ms=-10")
        self.client.get("/slow?ms=999999")

        delays = [call.args[0] for call in self.fake_time.sleep.call_args_list]
        self.assertEqual(delays, [0.0, 30.0])

    def test_slow_rejects_a_non_numeric_delay(self):
        response = self.client.get("/slow?ms=abc")

        self.assertEqual(response.status_code, 400)
        self.fake_time.sleep.assert_not_called()

    def test_health_and_home_report_the_server_identity(self):
        health = self.client.get("/health")
        home = self.client.get("/")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "ok")
        self.assertEqual(home.get_json()["server"], "Backend-9999")


if __name__ == "__main__":
    unittest.main()
