import logging

from prometheus_client import Counter, Histogram, start_http_server

messages_produced = Counter(
    "benchmark_messages_produced", "Total number of messages successfully produced.", ["broker", "producer_id"]
)
bytes_produced = Counter(
    "benchmark_bytes_produced", "Total bytes of message payloads produced.", ["broker", "producer_id"]
)
produce_errors = Counter(
    "benchmark_produce_errors", "Total number of produce errors.", ["broker", "producer_id"]
)
produce_ack_latency = Histogram(
    "benchmark_produce_ack_latency_seconds",
    "Time between send and broker acknowledgement.",
    ["broker", "producer_id"],
    buckets=[.0001, .0005, .001, .005, .01, .025, .05, .1, .25, .5, 1, 2.5],
)


def serve_metrics(addr: str) -> None:
    """Serve /metrics from a background thread (like Go's `go serveMetrics`)."""
    port = int(addr.rsplit(":", 1)[-1])
    start_http_server(port)
    logging.info("metrics listening on %s", addr)
