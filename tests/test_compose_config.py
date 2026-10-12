"""Static checks on docker-compose.yml.

These do NOT start Docker. They catch the usual Compose mistakes: a service
name that does not exist, 127.0.0.1 used for container-to-container traffic,
and healthchecks or ports that do not match the service. They are skipped if
PyYAML is not installed.
"""

import importlib.util
import os
import re
import unittest

COMPOSE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docker-compose.yml"
)

HAS_YAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAS_YAML, "PyYAML is not installed")
class ComposeConfigTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import yaml

        with open(COMPOSE) as compose_file:
            cls.services = yaml.safe_load(compose_file)["services"]

    def healthcheck_text(self, name):
        return " ".join(self.services[name]["healthcheck"]["test"])

    def test_expected_services_exist(self):
        self.assertEqual(
            set(self.services),
            {"backend-9001", "backend-9002", "backend-9003",
             "load-balancer", "dashboard"}
        )

    def test_host_ports_are_unique_and_unchanged(self):
        ports = [
            port for service in self.services.values()
            for port in service["ports"]
        ]

        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(
            set(ports),
            {"9001:9001", "9002:9002", "9003:9003", "8080:8080", "5050:5050"}
        )

    def test_each_backend_runs_on_the_port_in_its_name(self):
        for port in ("9001", "9002", "9003"):
            service = self.services[f"backend-{port}"]

            with self.subTest(backend=port):
                self.assertTrue(service["command"].endswith(f" {port}"))
                self.assertIn(f"{port}:{port}", service["ports"])
                self.assertIn(f":{port}/health", self.healthcheck_text(f"backend-{port}"))

    def test_load_balancer_reaches_backends_by_service_name(self):
        hosts = self.services["load-balancer"]["environment"][
            "VANTORA_BACKEND_HOSTS"
        ].split(",")

        # Same order as the ports 9001, 9002, 9003 in lb/balancer.py.
        self.assertEqual(hosts, ["backend-9001", "backend-9002", "backend-9003"])

        for host in hosts:
            self.assertIn(host, self.services)

    def test_dashboard_reaches_load_balancer_by_service_name(self):
        environment = self.services["dashboard"]["environment"]

        self.assertEqual(environment["LB_URL"], "http://load-balancer:8080")
        self.assertIn("load-balancer", self.services)

    def test_no_container_to_container_address_uses_localhost(self):
        for name in ("load-balancer", "dashboard"):
            for key, value in self.services[name]["environment"].items():
                with self.subTest(service=name, setting=key):
                    if key in ("LB_URL", "VANTORA_BACKEND_HOSTS"):
                        self.assertNotRegex(str(value), r"127\.0\.0\.1|localhost")

    def test_dashboard_listens_on_all_interfaces_without_debug(self):
        environment = self.services["dashboard"]["environment"]

        self.assertEqual(environment["DASHBOARD_HOST"], "0.0.0.0")
        self.assertNotIn(
            str(environment.get("DASHBOARD_DEBUG", "")).lower(),
            ("1", "true", "yes", "on")
        )

    def test_startup_waits_for_healthy_dependencies(self):
        expectations = {
            "load-balancer": ["backend-9001", "backend-9002", "backend-9003"],
            "dashboard": ["load-balancer"],
        }

        for name, dependencies in expectations.items():
            depends_on = self.services[name]["depends_on"]

            for dependency in dependencies:
                with self.subTest(service=name, depends_on=dependency):
                    self.assertEqual(
                        depends_on[dependency]["condition"], "service_healthy"
                    )
                    self.assertIn("healthcheck", self.services[dependency])

    def test_load_balancer_and_dashboard_healthchecks_target_their_own_ports(self):
        self.assertIn(":8080/stats", self.healthcheck_text("load-balancer"))
        self.assertIn(":5050/healthz", self.healthcheck_text("dashboard"))

    def test_every_service_has_a_build_context_that_exists(self):
        root = os.path.dirname(COMPOSE)

        for name, service in self.services.items():
            with self.subTest(service=name):
                context = os.path.join(root, service["build"])
                self.assertTrue(os.path.isfile(os.path.join(context, "Dockerfile")))

    def test_dockerfiles_copy_only_files_that_exist(self):
        root = os.path.dirname(COMPOSE)

        for name, service in self.services.items():
            context = os.path.join(root, service["build"])

            with open(os.path.join(context, "Dockerfile")) as dockerfile:
                for line in dockerfile:
                    match = re.match(r"COPY\s+(\S+)\s+\S+", line)

                    if match:
                        with self.subTest(service=name, copies=match.group(1)):
                            self.assertTrue(
                                os.path.exists(os.path.join(context, match.group(1)))
                            )


if __name__ == "__main__":
    unittest.main()
