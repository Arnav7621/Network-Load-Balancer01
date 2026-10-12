# Wireshark traffic evidence

This folder holds the packet-level evidence for Vantora and the tools to
produce more of it.

| File | What it is |
|---|---|
| `load-balancer-to-backends.pcap` | Existing capture of the load balancer talking to the three backends (inside the Docker network). |
| `backend-traffic-evidence.png` | Existing Wireshark screenshot of that capture. |
| `../../client-to-load-balancer.pcapng` | Existing capture of a client talking to the load balancer on port 8080 (repository root). |
| `generate_traffic.py` | Sends a controlled batch of requests so a capture matches a known workload. |
| `analyze_capture.py` | Reads a `.pcap`/`.pcapng` and prints the numbers you should see in Wireshark. |

## What the existing captures show (and do not show)

Running `analyze_capture.py` on the committed files gives:

| Capture | Client GETs | Health probes | Who answered |
|---|---|---|---|
| `load-balancer-to-backends.pcap` (1,884 packets) | 10, **all to port 9001** | 49 to each of 9001, 9002, 9003 | – |
| `client-to-load-balancer.pcapng` (331 packets) | 20 to port 8080 | – | **20 of 20 by `Backend-9001`** |

They are genuine captures and they do show the reverse-proxy behaviour: the
client connects to the load balancer, and the load balancer opens its own
connection to a backend. But **every client request went to Backend-9001**, so
they do not show requests being *distributed*. That is what Least Connections
does with requests that arrive one after another: nothing overlaps, every
backend has 0 active connections, and the tie goes to the first backend.

The procedure below produces captures that do show distribution and failover.
**These new captures have not been taken yet. They must be recorded by you**
(see "Evidence still to capture").

## Setup for the captures: run Vantora natively

On macOS and Windows, Docker Desktop runs containers inside a virtual machine,
so Wireshark on your computer cannot see traffic between containers. For the
capture sessions, run the three backends and the load balancer directly on
your computer. All traffic is then on the loopback interface, where one
capture sees every connection.

From the project root, in four terminals (see the main `README.md` for the
virtual-environment setup):

```bash
python backend/app.py 9001
python backend/app.py 9002
python backend/app.py 9003
python lb/balancer.py
```

Start Wireshark, choose the loopback interface (`lo0` on macOS, `Loopback` on
Windows, `lo` on Linux) and set this **capture filter** so only Vantora's
ports are recorded:

```text
tcp port 8080 or tcp port 9001 or tcp port 9002 or tcp port 9003
```

Command-line alternative (Linux/macOS, may need `sudo`):

```bash
tcpdump -i lo0 -w round-robin-distribution.pcap \
    tcp port 8080 or tcp port 9001 or tcp port 9002 or tcp port 9003
```

(use `-i lo` on Linux). Start the capture, run one recipe below, wait a second,
then stop the capture and save it.

## Recipes

Each recipe is one capture. Run them from the project root.

### A. Round Robin distribution

```bash
python3 docs/wireshark/generate_traffic.py --algorithm "Round Robin" --requests 30
```

Expected: the script prints **10 / 10 / 10** for Backend-9001, 9002 and 9003.
In the capture, 30 client requests reach port 8080 and the load balancer sends
10 to each of ports 9001, 9002 and 9003. (This split is exact because 30 is a
multiple of 3.)

### B. Weighted Round Robin distribution

```bash
python3 docs/wireshark/generate_traffic.py --algorithm "Weighted Round Robin" --requests 30
```

Expected: **15 / 10 / 5** (the weights are 3, 2 and 1, set in `lb/balancer.py`).

### C. Least Connections distribution

Least Connections only spreads traffic when requests overlap, so use slow
requests and several at once:

```bash
python3 docs/wireshark/generate_traffic.py --algorithm "Least Connections" \
    --path "/slow?ms=500" --concurrency 6 --requests 18
```

Expected: all three backends receive requests, about **6 / 6 / 6**. The split is
not guaranteed to be exact because it depends on how the requests overlap.

For contrast (and to explain the existing captures), run it without overlap:

```bash
python3 docs/wireshark/generate_traffic.py --algorithm "Least Connections" --requests 18
```

Expected: **18 / 0 / 0**, everything on Backend-9001.

### D. Failover

1. Stop one backend (press Ctrl+C in the terminal running `backend/app.py 9002`).
2. Start the capture, then run:

   ```bash
   python3 docs/wireshark/generate_traffic.py --algorithm "Round Robin" --requests 12
   ```

Expected: all 12 requests return **200** and Backend-9002 never answers. The
traffic is shared unevenly between 9001 and 9003 (8 and 4 was observed),
because each refused attempt is retried on the next backend. In the capture,
connection attempts to port 9002 are answered with a TCP **RST** (connection
refused). The load balancer makes three such attempts on behalf of clients,
then its circuit breaker for 9002 opens and it stops trying. The health
checker also connects to 9002 every 2 seconds, so a few more RST packets
appear for as long as the capture runs.

### E. Recovery

1. Start `python backend/app.py 9002` again.
2. Start the capture, wait about 16 seconds (two good health checks plus the
   10-second circuit-breaker cooldown), then run:

   ```bash
   python3 docs/wireshark/generate_traffic.py --requests 12
   ```

Expected: Backend-9002 answers again, about **4 / 4 / 4**, and `GET /` requests
to port 9002 reappear in the capture.

## Wireshark display filters

| To see | Display filter |
|---|---|
| Client requests, as they reach the load balancer | `http.request.method == "GET" && tcp.dstport == 8080` |
| Requests the load balancer forwards to backends | `http.request.method == "GET" && http.request.uri != "/health" && tcp.dstport in {9001 9002 9003}` |
| Requests forwarded to one backend | `http.request.uri != "/health" && tcp.dstport == 9002` |
| Health-check probes | `http.request.uri == "/health"` |
| Which backend answered (response header added by the load balancer) | `http.response && http contains "X-Served-By"` |
| A stopped backend refusing connections (recipe D) | `tcp.srcport == 9002 && tcp.flags.reset == 1` |

Useful windows:

- **Statistics > Conversations > TCP**: tick "Limit to display filter" to count
  connections per port.
- **Right-click a request > Follow > TCP Stream**: shows the client's
  connection and, separately, the load balancer's connection to the backend.
  Two different TCP connections with different source ports are the proof that
  this is a reverse proxy, not packet forwarding.

## Check a capture with the analyzer

```bash
pip install scapy
python3 docs/wireshark/analyze_capture.py round-robin-distribution.pcap
```

For recipe A it should print 30 client GETs on port 8080, 10 on each backend
port, and `Backends that received client requests: 3 of 3`. It works on
`.pcap` and `.pcapng` files. Use `--lb-port` and `--backend-ports` if you
changed the ports.

## Evidence still to capture

Record these yourself, so the evidence is genuinely yours:

| Capture to save in this folder | Recipe | Screenshot to take |
|---|---|---|
| `round-robin-distribution.pcapng` | A | The Conversations window or a filtered view showing requests to 9001, 9002 and 9003 |
| `weighted-distribution.pcapng` | B | Same view, showing the 3:2:1 split |
| `least-connections-concurrent.pcapng` | C | Same view, showing the spread |
| `failover-backend-9002-down.pcapng` | D | Filter `tcp.srcport == 9002 && tcp.flags.reset == 1`, plus the 200 responses |
| `recovery-backend-9002.pcapng` | E | `GET /` requests to port 9002 reappearing |

Also take one **Follow TCP Stream** screenshot of a single proxied request.

Keep each capture short (a few hundred packets, well under 1 MB). Use the
capture filter above so nothing but Vantora's own localhost traffic is
recorded, and do not capture other interfaces.

## Capturing inside Docker

If you want to show the Docker network itself, the existing
`load-balancer-to-backends.pcap` is an example. The commands used to produce
it are not recorded in the repository, so write down the method you use next
time. One approach (not verified by the author of this guide) is to attach a
capture tool to the load balancer container's network:

```bash
docker run --rm --net=container:vantora-load-balancer \
    -v "$PWD:/captures" nicolaka/netshoot \
    tcpdump -i eth0 -w /captures/lb-backends.pcap \
    tcp port 9001 or tcp port 9002 or tcp port 9003
```

Inside the Docker network the backends are reached by service name
(`backend-9001`, ...) and the addresses are container addresses (for example
172.20.0.x), as in the existing screenshot.
