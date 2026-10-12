import json
import time
from dataclasses import dataclass


@dataclass
class Message:
    """Mirrors the producer payload. latency = now - publish_time_ns."""

    id: str
    publish_time_ns: int
    payload: str


def decode(data: bytes) -> Message:
    """Parse a message. Raises ValueError for anything that is not a valid message."""
    try:
        obj = json.loads(data)
        return Message(id=obj["id"], publish_time_ns=int(obj["publish_time_ns"]), payload=obj["payload"])
    except (KeyError, TypeError) as e:  # valid JSON of the wrong shape (a non-numeric string makes int() raise ValueError)
        raise ValueError(f"bad message shape: {e!r}") from e


def latency_seconds(m: Message) -> float:
    return (time.time_ns() - m.publish_time_ns) / 1e9
