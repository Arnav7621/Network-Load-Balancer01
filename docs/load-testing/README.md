# Load testing

## Files

| File | What it is |
|---|---|
| `benchmark-comparison.md` | The report: configuration, results table, observations, conclusion |
| `benchmark-runs/<algorithm>_stats.csv` | Raw Locust results, one file per algorithm. **The source of every figure in the report** |
| `benchmark-runs/<algorithm>_stats_history.csv` | Locust's per-second history for the same run |
| `benchmark-runs/<algorithm>_failures.csv`, `_exceptions.csv` | Failures and exceptions (empty: no failures were recorded) |
| `throughput-comparison.png`, `response-time-comparison.png`, `request-outcomes.png` | Graphs, drawn from the `_stats.csv` files |
| `generate_graphs.py` | Reads the `_stats.csv` files, prints the results table and redraws the graphs |
| `locust-results.txt` | An earlier, shorter test (see below) |
| `../../locustfile_benchmark.py` | The Locust workload used for the benchmark (`GET /`, no think time) |

`<algorithm>` is `round-robin`, `weighted-round-robin` or `least-connections`.

## Keeping the report and the data in step

The numbers in `benchmark-comparison.md` are produced from the raw CSV files,
not typed by hand. To rebuild them after any new run:

```bash
python3 docs/load-testing/generate_graphs.py
```

It prints the results table (copy it into the report) and rewrites the three
graphs. The automated test `tests/test_benchmark_report.py` fails if the
report's table stops matching the CSV files.

Rounding used everywhere: requests as whole numbers, throughput and average
response time to one decimal place, median, P95, P99 and maximum as whole
milliseconds. Locust reports percentiles above 1 second in coarse steps, so
the 99.9th percentile of "1,000 ms" is an approximation.

## Running a benchmark

Start Vantora, then set the algorithm and run Locust for each algorithm in
turn. Example for Round Robin:

```bash
curl -X POST http://localhost:8080/algorithm \
     -H "Content-Type: application/json" -d '{"algorithm": "Round Robin"}'

locust -f locustfile_benchmark.py --headless -u 20 -r 5 -t 60s \
       --host http://127.0.0.1:8080 --csv docs/load-testing/benchmark-runs/round-robin
```

The options are 20 users (`-u`), 5 new users per second (`-r`) and 60 seconds
(`-t`), matching the recorded configuration. The command is not recorded in
the original report; it was checked to run and to write the four file names
listed above. `--csv <prefix>` produces `<prefix>_stats.csv`,
`<prefix>_stats_history.csv`, `<prefix>_failures.csv` and
`<prefix>_exceptions.csv`.

## What the benchmark can and cannot show

- It was run once per algorithm, with 20 users for 60 seconds, so there is no
  measure of run-to-run variation.
- All three backends are identical and the request is a trivial `GET /`, so
  the algorithms have nothing to distinguish between. The differences in the
  report (under 2% in throughput, a fraction of a millisecond in average
  response time) are within what repeated runs of the same algorithm would
  probably show. The report's conclusion says this.
- Locust, the load balancer and the backends share one computer, and the load
  balancer is a Python thread-per-request proxy, so the throughput figure
  mostly reflects the load balancer's own overhead.
- The distribution of requests across backends was not recorded for the
  benchmark. For distribution, see the packet-capture recipes in
  `../wireshark/README.md`, which show Round Robin 10/10/10, Weighted Round
  Robin 15/10/5, and Least Connections spreading overlapping requests.

A benchmark that could separate the algorithms would need backends with
different or varying response times (for example mixing `GET /` with
`GET /slow?ms=...`). That has not been run, and no results for it are
claimed.

## `locust-results.txt`

An earlier test with a lighter workload (`../../locustfile.py`, which waits
0.5 to 1.5 seconds between requests, so 20 users produce only about 20
requests per second). Its runs were of unequal length (400 to 987 requests)
and it was not used for the report. It is kept as a record of the first
smoke test; **do not compare its figures with the benchmark** and do not cite
it as the performance result.
