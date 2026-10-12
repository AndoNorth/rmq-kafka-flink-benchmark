import time
import unittest

from message import Message, decode, latency_seconds

# Bytes produced by the Go producer's json.Marshal for the same struct.
GO_SAMPLE = b'{"id":"abc","publish_time_ns":1700000000000000000,"payload":"\\u0000\\u0000"}'


class MessageTest(unittest.TestCase):
    def test_decodes_go_encoded_message(self):
        m = decode(GO_SAMPLE)
        self.assertEqual(m, Message(id="abc", publish_time_ns=1700000000000000000, payload="\x00\x00"))

    def test_malformed_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            decode(b"not json")

    def test_empty_body_raises_value_error(self):
        with self.assertRaises(ValueError):
            decode(b"")

    def test_missing_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            decode(b'{"id":"abc"}')

    def test_latency_is_seconds_since_publish(self):
        m = Message(id="a", publish_time_ns=time.time_ns() - 2_000_000_000, payload="")
        self.assertAlmostEqual(latency_seconds(m), 2.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
