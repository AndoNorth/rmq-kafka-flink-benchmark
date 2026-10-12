import logging

from prometheus_client import Counter, Histogram, start_http_server

messages_consumed = Counter(
    "benchmark_messages_consumed", "Total number of messages consumed.", ["broker", "consumer_id"]
)
bytes_consumed = Counter(
    "benchmark_bytes_consumed", "Total bytes of message payloads consumed.", ["broker", "consumer_id"]
)
consume_errors = Counter(
    "benchmark_consume_errors", "Total number of consume or decode errors.", ["broker", "consumer_id"]
)
# Full publish-to-consume path. Buckets are biased toward sub-second values.
e2e_latency = Histogram(
    "benchmark_e2e_latency_seconds",
    "End-to-end latency from producer publish to consumer receive.",
    ["broker"],
    buckets=[
        .0001, .0005, .001, .002, .005,
        .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 7.5, 10,
        25, 50, 100, 250, 500, 1000,
        2500, 5000, 10000,
        25000, 50000, 100000,
    ],
)


def serve_metrics(addr: str) -> None:
    """Serve /metrics from a background thread (like Go's `go serveMetrics`)."""
    port = int(addr.rsplit(":", 1)[-1])
    start_http_server(port)
    logging.info("metrics listening on %s", addr)
