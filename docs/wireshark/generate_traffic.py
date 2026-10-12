#!/usr/bin/env python3
"""Send a controlled batch of requests through Vantora and show who answered.

Use it while Wireshark (or tcpdump) is capturing, so the packets you record
match a known workload. It needs only the Python standard library.

Examples (run from the project root, with Vantora already running):

  # Round Robin: 30 sequential requests -> 10 / 10 / 10
  python3 docs/wireshark/generate_traffic.py --algorithm "Round Robin" --requests 30

  # Weighted Round Robin (weights 3:2:1) -> 15 / 10 / 5
  python3 docs/wireshark/generate_traffic.py --algorithm "Weighted Round Robin" --requests 30

  # Least Connections only spreads traffic when requests OVERLAP, so use
  # slow requests and several at once:
  python3 docs/wireshark/generate_traffic.py --algorithm "Least Connections" \\
      --path "/slow?ms=500" --concurrency 6 --requests 18
"""

import argparse
import collections
import http.client
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send requests through the Vantora load balancer."
    )
    parser.add_argument(
        "--lb", default="http://127.0.0.1:8080",
        help="load balancer address (default: %(default)s)"
    )
    parser.add_argument(
        "--algorithm", choices=[
            "Round Robin", "Weighted Round Robin", "Least Connections"
        ],
        help="switch the load balancer to this algorithm first"
    )
    parser.add_argument(
        "--requests", type=int, default=30,
        help="number of requests to send (default: %(default)s)"
    )
    parser.add_argument(
        "--path", default="/",
        help="path to request (default: %(default)s)"
    )
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help="requests in flight at the same time (default: %(default)s)"
    )
    parser.add_argument(
        "--pause", type=float, default=0.0,
        help="seconds to wait between requests when --concurrency is 1"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="seconds to wait for each response (default: %(default)s)"
    )
    return parser.parse_args()


def request(base_url, method, path, body=None, timeout=30.0):
    """Return (status, served_by, response_body) for one HTTP request."""
    target = urlparse(base_url)
    connection = http.client.HTTPConnection(
        target.hostname, target.port or 80, timeout=timeout
    )

    try:
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("X-Served-By"), response.read()
    finally:
        connection.close()


def set_algorithm(base_url, name):
    status, _, body = request(
        base_url, "POST", "/algorithm", json.dumps({"algorithm": name})
    )

    if status != 200:
        sys.exit(f"Could not switch algorithm ({status}): {body.decode()}")

    print(f"Algorithm set to: {name}")


def send_one(args, index):
    try:
        status, served_by, _ = request(
            args.lb, "GET", args.path, timeout=args.timeout
        )
        return index, status, served_by or "-"
    except OSError as error:
        return index, f"error ({type(error).__name__})", "-"


def main():
    args = parse_args()

    if args.algorithm:
        set_algorithm(args.lb, args.algorithm)

    print(
        f"Sending {args.requests} x GET {args.path} to {args.lb} "
        f"(concurrency {args.concurrency})"
    )

    started = time.time()
    results = []

    if args.concurrency <= 1:
        for index in range(args.requests):
            results.append(send_one(args, index))

            if args.pause:
                time.sleep(args.pause)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(
                lambda index: send_one(args, index), range(args.requests)
            ))

    elapsed = time.time() - started

    served = collections.Counter(served_by for _, _, served_by in results)
    statuses = collections.Counter(str(status) for _, status, _ in results)

    print()
    print("Answered by:")

    for name in sorted(served):
        count = served[name]
        print(f"  {name:<14} {count:>4}  ({100 * count / len(results):.1f}%)")

    print()
    print("Status codes: " + ", ".join(
        f"{code} x{count}" for code, count in sorted(statuses.items())
    ))
    print(f"Finished in {elapsed:.1f} s")


if __name__ == "__main__":
    main()
