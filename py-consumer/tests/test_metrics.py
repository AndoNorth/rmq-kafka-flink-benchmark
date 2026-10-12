import unittest

from prometheus_client import generate_latest

import metrics


class MetricsTest(unittest.TestCase):
    def test_exposed_names_and_labels(self):
        metrics.messages_consumed.labels("redpanda", "c1").inc()
        metrics.bytes_consumed.labels("redpanda", "c1").inc(10)
        metrics.consume_errors.labels("redpanda", "c1").inc()
        metrics.e2e_latency.labels("redpanda").observe(0.01)
        text = generate_latest().decode()
        for name in (
            'benchmark_messages_consumed_total{broker="redpanda",consumer_id="c1"}',
            'benchmark_bytes_consumed_total{broker="redpanda",consumer_id="c1"}',
            'benchmark_consume_errors_total{broker="redpanda",consumer_id="c1"}',
            'benchmark_e2e_latency_seconds_bucket{broker="redpanda",le="0.0001"}',
            'le="100000.0"',
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
