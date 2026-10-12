#!/usr/bin/env python3
"""Summarise a Vantora packet capture, so you know what to expect in Wireshark.

It counts, from the packets themselves:
  * HTTP GET requests sent to the load balancer and to each backend, split
    into health-check probes (GET /health) and proxied client requests
    (other methods, such as the POST that switches algorithm, are listed
    separately),
  * which backend answered each client request (the X-Served-By header),
  * refused connections (TCP RST) coming from each backend port, which is
    what a stopped backend looks like to the load balancer.

Usage:
    python3 docs/wireshark/analyze_capture.py CAPTURE.pcap
    python3 docs/wireshark/analyze_capture.py CAPTURE.pcapng --lb-port 8080 \\
        --backend-ports 9001 9002 9003

Requires scapy:  pip install scapy
"""

import argparse
import collections
import re
import sys

REQUEST_LINE = re.compile(rb"^([A-Z]+) (\S+) HTTP/1\.[01]\r\n")
SERVED_BY = re.compile(rb"\r\nX-Served-By: ([^\r\n]+)", re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("capture", help="a .pcap or .pcapng file")
    parser.add_argument("--lb-port", type=int, default=8080)
    parser.add_argument(
        "--backend-ports", type=int, nargs="+", default=[9001, 9002, 9003]
    )
    args = parser.parse_args()

    try:
        from scapy.all import IP, IPv6, TCP, Raw, rdpcap
    except ImportError:
        sys.exit("scapy is required: pip install scapy")

    packets = rdpcap(args.capture)

    proxied = collections.Counter()      # port -> client GET requests (not /health)
    probes = collections.Counter()       # port -> GET /health
    other = collections.Counter()        # (method, uri) -> count, e.g. POST /algorithm
    served_by = collections.Counter()    # backend name -> responses to clients
    refused = collections.Counter()      # port -> RST packets sent by that port
    ports = [args.lb_port] + list(args.backend_ports)

    for packet in packets:
        if TCP not in packet:
            continue

        tcp = packet[TCP]

        # A backend port sending RST = connection refused / reset.
        if tcp.flags.R and tcp.sport in args.backend_ports:
            refused[tcp.sport] += 1

        if Raw not in packet:
            continue

        payload = bytes(packet[Raw].load)

        match = REQUEST_LINE.match(payload)

        if match and tcp.dport in ports:
            method, uri = match.group(1), match.group(2)

            if method != b"GET":
                other[(method.decode(), uri.decode(errors="replace"))] += 1
            elif uri == b"/health":
                probes[tcp.dport] += 1
            else:
                proxied[tcp.dport] += 1

        if tcp.sport == args.lb_port and payload.startswith(b"HTTP/"):
            header = SERVED_BY.search(payload)

            if header:
                served_by[header.group(1).decode()] += 1

    print(f"Capture: {args.capture}  ({len(packets)} packets)\n")

    print(f"{'Port':<8}{'Role':<16}{'Client GETs':>16}{'Health probes':>16}{'RST sent':>12}")

    for port in ports:
        role = "load balancer" if port == args.lb_port else "backend"
        print(
            f"{port:<8}{role:<16}{proxied[port]:>16}{probes[port]:>16}"
            f"{refused[port]:>12}"
        )

    if other:
        print(
            "\nOther requests (not counted above): "
            + ", ".join(f"{method} {uri} x{count}" for (method, uri), count in sorted(other.items()))
        )

    print()

    if served_by:
        total = sum(served_by.values())
        print("Responses to clients, by X-Served-By:")

        for name in sorted(served_by):
            print(
                f"  {name:<14}{served_by[name]:>5}"
                f"  ({100 * served_by[name] / total:.1f}%)"
            )
    else:
        print(
            "No client responses with an X-Served-By header were found "
            "(is the load-balancer port capture included?)."
        )

    backends_used = [
        port for port in args.backend_ports if proxied[port] > 0
    ]

    print(
        f"\nBackends that received client requests: "
        f"{len(backends_used)} of {len(args.backend_ports)}"
        f"  {backends_used}"
    )


if __name__ == "__main__":
    main()
