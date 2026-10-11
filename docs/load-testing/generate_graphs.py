"""Generate the Vantora benchmark graphs from the raw Locust CSV files.

Every number comes from the "Aggregated" row of
benchmark-runs/<algorithm>_stats.csv. Nothing is typed in by hand, so the
graphs and the printed table always match the recorded data.

Run from any directory:

    python3 docs/load-testing/generate_graphs.py

Requires: matplotlib, numpy
"""

import csv
import os

import matplotlib

matplotlib.use("Agg")  # write PNG files without needing a display

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(OUTPUT_DIR, "benchmark-runs")

# (label shown on graphs, file-name prefix in benchmark-runs/)
ALGORITHMS = [
    ("Round Robin", "round-robin"),
    ("Weighted Round Robin", "weighted-round-robin"),
    ("Least Connections", "least-connections"),
]

LABELS = [label for label, _ in ALGORITHMS]


def load_run(prefix):
    """Return the aggregated Locust metrics for one benchmark run."""
    path = os.path.join(RUNS_DIR, f"{prefix}_stats.csv")

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Benchmark file not found: {path}")

    with open(path, newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            if row["Name"] == "Aggregated":
                total = int(row["Request Count"])
                failed = int(row["Failure Count"])

                return {
                    "requests": total,
                    "failed": failed,
                    "successful": total - failed,
                    "throughput": float(row["Requests/s"]),
                    "average_ms": float(row["Average Response Time"]),
                    "median_ms": float(row["Median Response Time"]),
                    "p95_ms": float(row["95%"]),
                    "p99_ms": float(row["99%"]),
                    "max_ms": float(row["Max Response Time"]),
                }

    raise ValueError(f"No 'Aggregated' row found in {path}")


def print_markdown_table(runs):
    """Print the results table used in benchmark-comparison.md.

    Rounding rules (used everywhere):
      requests            whole numbers, thousands separators
      throughput          req/s, 1 decimal
      average             ms, 1 decimal
      median/P95/P99/max  ms, whole numbers
    """
    rows = [
        ("Total requests", lambda r: f"{r['requests']:,}"),
        ("Failed requests", lambda r: f"{r['failed']:,}"),
        ("Failure rate", lambda r: f"{100 * r['failed'] / r['requests']:.1f}%"),
        ("Average response time", lambda r: f"{r['average_ms']:.1f} ms"),
        ("Median response time", lambda r: f"{r['median_ms']:.0f} ms"),
        ("P95 response time", lambda r: f"{r['p95_ms']:.0f} ms"),
        ("P99 response time", lambda r: f"{r['p99_ms']:.0f} ms"),
        ("Maximum response time", lambda r: f"{r['max_ms']:,.0f} ms"),
        ("Throughput", lambda r: f"{r['throughput']:,.1f} req/s"),
    ]

    print("| Metric | " + " | ".join(LABELS) + " |")
    print("|---|" + "---:|" * len(LABELS))

    for name, fmt in rows:
        print(f"| {name} | " + " | ".join(fmt(r) for r in runs) + " |")


def style_axes(ax, ylabel):
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)


def label_bars(ax, bars, values, fmt):
    for bar, value in zip(bars, values):
        ax.annotate(
            fmt(value),
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
        )


def save(fig, filename):
    plt.tight_layout()
    fig.savefig(os.path.join(OUTPUT_DIR, filename), dpi=200)
    plt.close(fig)
    print(f"Created: {filename}")


def main():
    runs = [load_run(prefix) for _, prefix in ALGORITHMS]

    print("Values read from benchmark-runs/*_stats.csv:\n")
    print_markdown_table(runs)
    print()

    # Graph 1: throughput (y-axis starts at 0 so differences are not exaggerated)
    fig, ax = plt.subplots(figsize=(9, 5))
    values = [r["throughput"] for r in runs]
    bars = ax.bar(LABELS, values)
    ax.set_title("Vantora: Throughput Comparison")
    style_axes(ax, "Requests per second")
    label_bars(ax, bars, values, lambda v: f"{v:,.1f}")
    plt.xticks(rotation=10, ha="right")
    save(fig, "throughput-comparison.png")

    # Graph 2: response times
    x = np.arange(len(LABELS))
    width = 0.24
    series = [
        ("Average", [r["average_ms"] for r in runs], -width, lambda v: f"{v:.1f}"),
        ("P95", [r["p95_ms"] for r in runs], 0, lambda v: f"{v:.0f}"),
        ("P99", [r["p99_ms"] for r in runs], width, lambda v: f"{v:.0f}"),
    ]

    fig, ax = plt.subplots(figsize=(9, 5))
    for name, values, offset, fmt in series:
        bars = ax.bar(x + offset, values, width, label=name)
        label_bars(ax, bars, values, fmt)
    ax.set_title("Vantora: Response Time Comparison")
    style_axes(ax, "Response time (ms)")
    ax.set_xticks(x)
    ax.set_xticklabels(LABELS, rotation=10, ha="right")
    ax.legend()
    save(fig, "response-time-comparison.png")

    # Graph 3: successful and failed requests
    successful = [r["successful"] for r in runs]
    failed = [r["failed"] for r in runs]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(LABELS, successful, label="Successful")
    ax.bar(LABELS, failed, bottom=successful, label="Failed")
    ax.set_title("Vantora: Request Outcomes")
    style_axes(ax, "Number of requests")
    label_bars(ax, bars, successful, lambda v: f"{v:,}")
    ax.set_ylim(0, max(r["requests"] for r in runs) * 1.25)  # room for labels and legend
    ax.legend(loc="upper center", ncol=2)
    plt.xticks(rotation=10, ha="right")
    save(fig, "request-outcomes.png")

    print("\nAll graphs saved in:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
