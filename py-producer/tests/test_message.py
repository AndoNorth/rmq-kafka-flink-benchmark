import json
import unittest

from message import Message, encode, new_message


class MessageTest(unittest.TestCase):
    def test_keys_match_go_struct(self):
        m = new_message("abc", 2)
        data = json.loads(encode(m))
        self.assertEqual(set(data), {"id", "publish_time_ns", "payload"})
        self.assertEqual(data["id"], "abc")
        self.assertIsInstance(data["publish_time_ns"], int)

    def test_payload_is_nul_bytes_of_requested_size(self):
        self.assertEqual(new_message("x", 5).payload, "\x00" * 5)

    def test_zero_size_payload(self):
        data = json.loads(encode(new_message("x", 0)))
        self.assertEqual(data["payload"], "")

    def test_encoding_is_compact_and_matches_go_bytes(self):
        m = Message(id="abc", publish_time_ns=1700000000000000000, payload="\x00\x00")
        self.assertEqual(
            encode(m),
            b'{"id":"abc","publish_time_ns":1700000000000000000,"payload":"\\u0000\\u0000"}',
        )


if __name__ == "__main__":
    unittest.main()
