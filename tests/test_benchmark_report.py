"""Keeps docs/load-testing/benchmark-comparison.md in step with the raw data.

The expected values are calculated here straight from the Locust CSV files
(an independent implementation of the rounding rules in generate_graphs.py),
so the report cannot drift away from the recorded results again.
"""

import csv
import os
import unittest

LOAD_TESTING = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "load-testing"
)

RUNS = [
    ("Round Robin", "round-robin"),
    ("Weighted Round Robin", "weighted-round-robin"),
    ("Least Connections", "least-connections"),
]


def aggregated_row(prefix):
    path = os.path.join(LOAD_TESTING, "benchmark-runs", f"{prefix}_stats.csv")

    with open(path, newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            if row["Name"] == "Aggregated":
                return row

    raise AssertionError(f"No Aggregated row in {path}")


def markdown_table():
    """Return {metric name: [value for each algorithm]} from the report."""
    path = os.path.join(LOAD_TESTING, "benchmark-comparison.md")
    table = {}

    with open(path) as report:
        for line in report:
            if not line.startswith("|"):
                continue

            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]

            if set("".join(cells)) <= set("-:"):      # the |---|---| row
                continue

            table[cells[0]] = cells[1:]

    return table


class BenchmarkReportTests(unittest.TestCase):

    def test_results_table_matches_the_raw_csv_files(self):
        rows = [aggregated_row(prefix) for _, prefix in RUNS]

        expected = {
            "Total requests": [f"{int(r['Request Count']):,}" for r in rows],
            "Failed requests": [f"{int(r['Failure Count']):,}" for r in rows],
            "Failure rate": [
                f"{100 * int(r['Failure Count']) / int(r['Request Count']):.1f}%"
                for r in rows
            ],
            "Average response time": [
                f"{float(r['Average Response Time']):.1f} ms" for r in rows
            ],
            "Median response time": [
                f"{float(r['Median Response Time']):.0f} ms" for r in rows
            ],
            "P95 response time": [f"{float(r['95%']):.0f} ms" for r in rows],
            "P99 response time": [f"{float(r['99%']):.0f} ms" for r in rows],
            "Maximum response time": [
                f"{float(r['Max Response Time']):,.0f} ms" for r in rows
            ],
            "Throughput": [
                f"{float(r['Requests/s']):,.1f} req/s" for r in rows
            ],
        }

        table = markdown_table()

        for metric, values in expected.items():
            with self.subTest(metric=metric):
                self.assertIn(metric, table, f"{metric} missing from the report")
                self.assertEqual(table[metric], values)

    def test_report_names_the_same_algorithms_as_the_csv_files(self):
        with open(os.path.join(LOAD_TESTING, "benchmark-comparison.md")) as report:
            header = next(line for line in report if line.startswith("| Metric"))

        names = [cell.strip() for cell in header.strip().strip("|").split("|")][1:]

        self.assertEqual(names, [label for label, _ in RUNS])

    def test_every_graph_exists_and_is_not_empty(self):
        for graph in (
            "throughput-comparison.png",
            "response-time-comparison.png",
            "request-outcomes.png",
        ):
            with self.subTest(graph=graph):
                path = os.path.join(LOAD_TESTING, graph)
                self.assertTrue(os.path.isfile(path))
                self.assertGreater(os.path.getsize(path), 10_000)


if __name__ == "__main__":
    unittest.main()
