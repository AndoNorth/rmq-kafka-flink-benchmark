import unittest

from prometheus_client import generate_latest

import metrics


class MetricsTest(unittest.TestCase):
    def test_exposed_names_and_labels(self):
        metrics.messages_produced.labels("redpanda", "p1").inc()
        metrics.bytes_produced.labels("redpanda", "p1").inc(10)
        metrics.produce_errors.labels("redpanda", "p1").inc()
        metrics.produce_ack_latency.labels("redpanda", "p1").observe(0.01)
        text = generate_latest().decode()
        for name in (
            'benchmark_messages_produced_total{broker="redpanda",producer_id="p1"}',
            'benchmark_bytes_produced_total{broker="redpanda",producer_id="p1"}',
            'benchmark_produce_errors_total{broker="redpanda",producer_id="p1"}',
            'benchmark_produce_ack_latency_seconds_bucket{broker="redpanda",le="0.0001",producer_id="p1"}',
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
