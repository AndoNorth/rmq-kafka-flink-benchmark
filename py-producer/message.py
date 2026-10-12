import json
import time
from dataclasses import asdict, dataclass


@dataclass
class Message:
    """Benchmark event. publish_time_ns lets the consumer compute E2E latency."""

    id: str
    publish_time_ns: int  # Unix nanoseconds at publish time
    payload: str


def new_message(id: str, payload_size: int) -> Message:
    return Message(id=id, publish_time_ns=time.time_ns(), payload="\x00" * payload_size)


def encode(m: Message) -> bytes:
    # Matches Go's json.Marshal bytes for this payload (NUL characters, ASCII ids),
    # so bytes_produced stays comparable. Not a general match: Go writes non-ASCII
    # as raw UTF-8 and escapes <, > and &, Python does the opposite.
    return json.dumps(asdict(m), separators=(",", ":")).encode()
