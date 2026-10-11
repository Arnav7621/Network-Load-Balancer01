# Vantora Load Balancing Performance Comparison

## Test Configuration

- **Date:** 10 October 2026
- **Tool:** Locust 2.34.0
- **Target:** `http://127.0.0.1:8080`
- **Request:** `GET /`
- **Concurrent users:** 20
- **Spawn rate:** 5 users per second
- **Duration:** 60 seconds per algorithm
- **Artificial wait:** None
- **Backend setup:** Three backends managed by Docker Compose

## Results

All figures below are calculated from the raw Locust files in `benchmark-runs/*_stats.csv` (the `Aggregated` row) by `generate_graphs.py`, so the table, the graphs and the raw data agree. Throughput is in requests per second, times are in milliseconds, and averages are shown to one decimal place.

| Metric | Round Robin | Weighted Round Robin | Least Connections |
|---|---:|---:|---:|
| Total requests | 85,234 | 85,756 | 84,356 |
| Failed requests | 0 | 0 | 0 |
| Failure rate | 0.0% | 0.0% | 0.0% |
| Average response time | 11.9 ms | 11.7 ms | 11.9 ms |
| Median response time | 10 ms | 10 ms | 11 ms |
| P95 response time | 15 ms | 15 ms | 15 ms |
| P99 response time | 21 ms | 21 ms | 20 ms |
| Maximum response time | 3,212 ms | 1,015 ms | 3,011 ms |
| Throughput | 1,440.7 req/s | 1,450.8 req/s | 1,425.0 req/s |

To regenerate the graphs and print this table again, run `python3 docs/load-testing/generate_graphs.py` (requires `matplotlib` and `numpy`).

## Observations

1. All three algorithms completed their benchmark runs with zero failed requests.
2. Weighted Round Robin recorded the highest throughput in these individual runs, at approximately 1,451 requests per second. That is about 0.7% above Round Robin and about 1.8% above Least Connections.
3. Average response times were between 11.7 ms and 11.9 ms for all three algorithms.
4. All three recorded a P95 of 15 ms. P99 was 21 ms for Round Robin and Weighted Round Robin, and 20 ms for Least Connections.
5. Maximum response times were far higher than the P99 values (3,212 ms, 1,015 ms and 3,011 ms), and the 99.9th percentile reported by Locust was about 1,000 ms in all three runs. This indicates that roughly one request in a thousand was very slow, so occasional latency spikes occurred in each run.

## Conclusion

Under the tested conditions, all three routing algorithms handled the GET workload successfully. Weighted Round Robin recorded the highest throughput in these individual runs, but the difference was small. These results do not establish that one algorithm is universally superior.

Only one run was conducted per algorithm. Repeated runs under controlled conditions would provide a stronger comparison. This benchmark primarily measures throughput for a lightweight GET endpoint and does not establish performance under every workload.

## Raw Results

Individual Locust CSV files are stored in `benchmark-runs/`:

- `round-robin`
- `weighted-round-robin`
- `least-connections`
