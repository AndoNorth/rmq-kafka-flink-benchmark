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
    # Compact separators and ASCII escaping reproduce Go's json.Marshal bytes,
    # so bytes_produced stays comparable between the two implementations.
    return json.dumps(asdict(m), separators=(",", ":")).encode()
