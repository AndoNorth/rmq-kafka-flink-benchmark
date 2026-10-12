# Python asyncio Producer and Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python asyncio producer and consumer that behave exactly like the Go ones, for Redpanda and RabbitMQ super-streams.

**Architecture:** Two small Python programs, `py-producer/` and `py-consumer/`, mirroring the Go layout (`main`, `message`, `metrics`, one file per broker). Each broker module exposes `async def run(stop: asyncio.Event)`, the counterpart of Go's `run(ctx)`. Metrics are served by `prometheus_client` on `:2112`, so Prometheus and Grafana need no change.

**Tech Stack:** Python 3.12, `aiokafka==0.14.0`, `rstream==1.1.0`, `prometheus_client==0.26.0`, stdlib `unittest`, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-12-python-async-producer-consumer-design.md`

## Global Constraints

- Env vars identical to Go: `BROKER_TYPE` (`redpanda`|`kafka`|`rabbitmq`, default `redpanda`), `METRICS_ADDR` (default `:2112`), `BROKER_ADDR` (default `redpanda:9092`), `BROKER_HOST` (`rabbitmq`), `BROKER_PORT` (`5552`), `BROKER_USER` (`guest`), `BROKER_PASS` (`guest`), `TOPIC` (`benchmark`), `PARTITIONS` (`8`), `RATE_LIMIT` (`0`), `MSG_SIZE_BYTES` (`256`), `PRODUCER_ID` / `CONSUMER_ID` (8-char uuid), `CONSUMER_GROUP` (`benchmark-consumers`).
- JSON message keys exactly `id`, `publish_time_ns` (Unix nanoseconds, int), `payload`. Payload is `MSG_SIZE_BYTES` NUL characters.
- Metric names, labels and buckets exactly as in `producer/metrics.go` and `consumer/metrics.go`.
- RabbitMQ: consumer name `benchmark-consumer`, offset NEXT, single active consumer, producer application property `routing-key` = message id, connect retry every 3 seconds.
- Redpanda: consumer offset reset `latest`, producer key = message id.
- Service names and compose labels unchanged: `{broker}-producer`, `{broker}-consumer`.
- Never use the em dash. Comments describe intent, not tasks or plans.
- Comments that explain asyncio use the Go or C++ counterpart (goroutine, channel, mutex), per the spec's table.

## Review Focus

- `RATE_LIMIT=0` with a slow broker: unacked messages must not grow without bound (in-flight semaphore, Task 3 and 4).
- `MSG_SIZE_BYTES=0`: producer must still emit a valid message that the consumer decodes (Task 1).
- Malformed or empty message body on the consumer: count `consume_errors`, never crash the loop (Tasks 1, 6, 7).
- Broker not reachable at startup: retry instead of exiting (Task 5 and 7).
- SIGTERM while idle or at full speed: clean exit, no traceback (Task 8 E2E).

---

## Task 1: Message module and shared test setup

**Files:**
- Create: `py-producer/message.py`, `py-producer/env.py`, `py-producer/requirements.txt`, `py-producer/tests/test_message.py`
- Create: `py-consumer/message.py`, `py-consumer/env.py`, `py-consumer/requirements.txt`, `py-consumer/tests/test_message.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces (producer): `Message` dataclass (`id: str`, `publish_time_ns: int`, `payload: str`), `new_message(id: str, payload_size: int) -> Message`, `encode(m: Message) -> bytes`, `get_env(key: str, default: str) -> str`.
- Produces (consumer): `Message`, `decode(data: bytes) -> Message` (raises `ValueError` on bad JSON or missing keys), `latency_seconds(m: Message) -> float`, `get_env`.

Python note: `@dataclass` is like a Go struct with generated constructor. There is no goroutine or await in this task.

- [ ] **Step 1: Ignore Python artifacts**

Append to `.gitignore`:

```
.venv/
__pycache__/
```

- [ ] **Step 2: Create requirements and a venv**

`py-producer/requirements.txt`:

```
aiokafka==0.14.0
rstream==1.1.0
prometheus_client==0.26.0
```

`py-consumer/requirements.txt`: identical content.

Run:

```bash
for d in py-producer py-consumer; do python3 -m venv $d/.venv && $d/.venv/bin/pip install -q -r $d/requirements.txt; done
```

Expected: no errors.

- [ ] **Step 3: Write the failing producer test**

`py-producer/tests/test_message.py`:

```python
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
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd py-producer && .venv/bin/python -m unittest discover -s tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'message'`.

- [ ] **Step 5: Implement producer `message.py` and `env.py`**

`py-producer/message.py`:

```python
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
```

`py-producer/env.py`:

```python
import os


def get_env(key: str, default: str) -> str:
    # Like Go's getEnv: an empty value counts as unset.
    return os.environ.get(key) or default
```

- [ ] **Step 6: Run producer test to verify it passes**

Run: `cd py-producer && .venv/bin/python -m unittest discover -s tests -v`
Expected: 4 tests PASS.

- [ ] **Step 7: Write the failing consumer test**

`py-consumer/tests/test_message.py`:

```python
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
```

- [ ] **Step 8: Run it to verify it fails**

Run: `cd py-consumer && .venv/bin/python -m unittest discover -s tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'message'`.

- [ ] **Step 9: Implement consumer `message.py` and `env.py`**

`py-consumer/message.py`:

```python
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
        return Message(id=obj["id"], publish_time_ns=obj["publish_time_ns"], payload=obj["payload"])
    except (KeyError, TypeError) as e:  # valid JSON of the wrong shape
        raise ValueError(f"bad message shape: {e!r}") from e


def latency_seconds(m: Message) -> float:
    return (time.time_ns() - m.publish_time_ns) / 1e9
```

`py-consumer/env.py`: same content as `py-producer/env.py`.

(`json.JSONDecodeError` is already a `ValueError`, so malformed and empty input need no extra handling.)

- [ ] **Step 10: Run consumer test to verify it passes**

Run: `cd py-consumer && .venv/bin/python -m unittest discover -s tests -v`
Expected: 5 tests PASS.

- [ ] **Step 11: Commit**

```bash
git add .gitignore py-producer py-consumer
git commit -m "feat: python message encode/decode matching go wire format"
```

---

## Task 2: Metrics modules

**Files:**
- Create: `py-producer/metrics.py`, `py-producer/tests/test_metrics.py`
- Create: `py-consumer/metrics.py`, `py-consumer/tests/test_metrics.py`

**Interfaces:**
- Produces (producer): `messages_produced`, `bytes_produced`, `produce_errors` (Counter, labels `broker`, `producer_id`), `produce_ack_latency` (Histogram, same labels), `serve_metrics(addr: str) -> None`.
- Produces (consumer): `messages_consumed`, `bytes_consumed`, `consume_errors` (Counter, labels `broker`, `consumer_id`), `e2e_latency` (Histogram, label `broker`), `serve_metrics(addr: str) -> None`.

Python note: `prometheus_client` appends `_total` to Counter names itself, so the Counter is declared as `benchmark_messages_produced` and exposes `benchmark_messages_produced_total`. Declaring it with `_total` already in the name is also accepted and stripped, but we declare without it to avoid confusion.

- [ ] **Step 1: Write the failing producer test**

`py-producer/tests/test_metrics.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd py-producer && .venv/bin/python -m unittest tests.test_metrics -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 3: Implement producer `metrics.py`**

```python
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
```

- [ ] **Step 4: Run producer test to verify it passes**

Run: `cd py-producer && .venv/bin/python -m unittest tests.test_metrics -v`
Expected: PASS. The exposition format puts `le` between the declared labels (`broker`, `le`, `producer_id`); the test strings already account for that.

- [ ] **Step 5: Write the failing consumer test**

`py-consumer/tests/test_metrics.py`:

```python
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
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd py-consumer && .venv/bin/python -m unittest tests.test_metrics -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 7: Implement consumer `metrics.py`**

```python
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
```

- [ ] **Step 8: Run consumer test to verify it passes**

Run: `cd py-consumer && .venv/bin/python -m unittest tests.test_metrics -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add py-producer py-consumer
git commit -m "feat: python prometheus metrics matching go metric names"
```

---

## Task 3: Pacer (rate limiting and the yield point)

**Files:**
- Create: `py-producer/pacing.py`, `py-producer/tests/test_pacing.py`

**Interfaces:**
- Produces: `Pacer(rate: int, clock=time.monotonic, sleep=asyncio.sleep)` with `async def wait(self) -> None`. `rate=0` means unlimited. `MAX_IN_FLIGHT = 10_000` constant (bound on unacknowledged messages, used by both broker modules).

asyncio notes for this task:
- `await asyncio.sleep(d)` is the `time.Ticker` equivalent: it parks this task for `d` seconds and lets other tasks run.
- `await asyncio.sleep(0)` parks for zero time. It is a pure yield: "let other tasks run, then continue me". Without it, a loop that never truly waits would block ack callbacks from running (the starvation trap in the spec).
- The clock and sleep are injected so the test controls time (no real waiting).

- [ ] **Step 1: Write the failing test**

`py-producer/tests/test_pacing.py`:

```python
import asyncio
import unittest

from pacing import Pacer


class FakeTime:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    async def sleep(self, d):
        self.sleeps.append(d)
        self.t += d


class PacerTest(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_does_not_drift_when_work_takes_time(self):
        fake = FakeTime()
        pacer = Pacer(10, clock=fake.clock, sleep=fake.sleep)  # one message per 0.1s
        times = []
        for _ in range(5):
            await pacer.wait()
            times.append(fake.t)
            fake.t += 0.03  # simulated per-message work
        for i, t in enumerate(times):
            self.assertAlmostEqual(t, i * 0.1)

    async def test_no_catch_up_burst_after_a_long_stall(self):
        fake = FakeTime()
        pacer = Pacer(10, clock=fake.clock, sleep=fake.sleep)
        await pacer.wait()
        fake.t += 5.0  # long stall
        await pacer.wait()
        self.assertEqual(fake.sleeps[-1], 0)
        await pacer.wait()
        self.assertAlmostEqual(fake.sleeps[-1], 0.1)

    async def test_unlimited_still_yields_to_the_event_loop(self):
        ran = []

        async def other_task():
            ran.append(True)

        task = asyncio.create_task(other_task())
        await Pacer(0).wait()
        self.assertTrue(ran)
        await task


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd py-producer && .venv/bin/python -m unittest tests.test_pacing -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pacing'`.

- [ ] **Step 3: Implement `pacing.py`**

```python
import asyncio
import time

# Upper bound on messages sent but not yet acknowledged. Plays the role of the
# 10_000-slot confirmation channel in the Go RabbitMQ producer: it gives
# backpressure when the broker is slower than the send loop.
MAX_IN_FLIGHT = 10_000


class Pacer:
    """Fixed-rate pacing for the send loop. rate=0 means unlimited."""

    def __init__(self, rate: int, clock=time.monotonic, sleep=asyncio.sleep):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0

    async def wait(self) -> None:
        if not self._interval:
            # Unlimited: still yield once per message. asyncio only switches tasks
            # at an await, so without this the ack callbacks would never run.
            await self._sleep(0)
            return
        now = self._clock()
        # max() drops missed slots after a stall instead of bursting to catch up,
        # like a Go ticker dropping ticks for a slow receiver.
        slot = max(self._next, now)
        self._next = slot + self._interval
        await self._sleep(slot - now)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd py-producer && .venv/bin/python -m unittest tests.test_pacing -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add py-producer
git commit -m "feat: python producer pacer with injectable clock"
```

---

## Task 4: Redpanda producer

**Files:**
- Create: `py-producer/redpanda.py`

**Interfaces:**
- Consumes: `message.new_message`, `message.encode`, `env.get_env`, `metrics.*` (producer), `pacing.Pacer`, `pacing.MAX_IN_FLIGHT`.
- Produces: `async def run(stop: asyncio.Event) -> None` in `redpanda.py`.

asyncio notes:
- `await producer.send(...)` returns after the record is queued, and gives back a `Future`. The future completes when the broker acks. That is the Go `Produce(ctx, record, callback)` pair split in two: queue now, `add_done_callback` for the ack.
- A done-callback is a plain function called by the event loop. It runs between other tasks' awaits, so it needs no mutex to touch the metrics.
- `make_ack` returns a closure. The closure remembers `send_time` and `size` for that one message. This replaces the Go `sendTimes` map.
- `asyncio.Semaphore(n)` is a counting semaphore: `await acquire()` parks if all n permits are taken. The ack callback releases one.

No unit test: this is broker IO. It is verified end to end in Task 8.

- [ ] **Step 1: Implement `py-producer/redpanda.py`**

```python
import asyncio
import logging
import time
import uuid

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

import metrics
from env import get_env
from message import encode, new_message
from pacing import MAX_IN_FLIGHT, Pacer

BROKER = "redpanda"


async def ensure_topic(brokers: str, topic: str, partitions: int) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=brokers)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name=topic, num_partitions=partitions, replication_factor=1)])
    except TopicAlreadyExistsError:
        pass  # fine, another producer or redpanda-init created it
    except Exception as e:
        logging.warning("redpanda: create topic: %s (may already exist)", e)
    finally:
        await admin.close()


async def run(stop: asyncio.Event) -> None:
    brokers = get_env("BROKER_ADDR", "redpanda:9092")
    topic = get_env("TOPIC", "benchmark")
    partitions = int(get_env("PARTITIONS", "8"))
    rate_limit = int(get_env("RATE_LIMIT", "0"))
    msg_size = int(get_env("MSG_SIZE_BYTES", "256"))
    producer_id = get_env("PRODUCER_ID", uuid.uuid4().hex[:8])

    await ensure_topic(brokers, topic, partitions)

    # .labels() looks up a child series by dict; do it once, not per message.
    produced = metrics.messages_produced.labels(BROKER, producer_id)
    produced_bytes = metrics.bytes_produced.labels(BROKER, producer_id)
    errors = metrics.produce_errors.labels(BROKER, producer_id)
    ack_latency = metrics.produce_ack_latency.labels(BROKER, producer_id)

    in_flight = asyncio.Semaphore(MAX_IN_FLIGHT)
    pacer = Pacer(rate_limit)

    def make_ack(send_time: float, size: int):
        # Closure: remembers this message's send time and size for its ack.
        def on_ack(fut: asyncio.Future) -> None:
            in_flight.release()
            if fut.cancelled() or fut.exception() is not None:
                errors.inc()
                return
            produced.inc()
            produced_bytes.inc(size)
            ack_latency.observe(time.monotonic() - send_time)

        return on_ack

    producer = AIOKafkaProducer(bootstrap_servers=brokers)
    await producer.start()
    logging.info(
        "redpanda producer started: id=%s topic=%s partitions=%d rateLimit=%d msgSize=%d",
        producer_id, topic, partitions, rate_limit, msg_size,
    )
    try:
        while not stop.is_set():
            await pacer.wait()
            # Parks here if MAX_IN_FLIGHT messages are unacked (backpressure).
            await in_flight.acquire()
            msg_id = str(uuid.uuid4())
            data = encode(new_message(msg_id, msg_size))
            send_time = time.monotonic()
            try:
                fut = await producer.send(topic, value=data, key=msg_id.encode())
            except Exception as e:
                logging.error("redpanda: send: %s", e)
                errors.inc()
                in_flight.release()
                continue
            fut.add_done_callback(make_ack(send_time, len(data)))
    finally:
        # stop() flushes queued records, so the final acks still land in metrics.
        await producer.stop()
```

- [ ] **Step 2: Syntax check**

Run: `cd py-producer && .venv/bin/python -c "import redpanda"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add py-producer/redpanda.py
git commit -m "feat: python redpanda producer"
```

---

## Task 5: RabbitMQ super-stream producer

**Files:**
- Create: `py-producer/rabbitmq.py`

**Interfaces:**
- Consumes: same as Task 4.
- Produces: `async def run(stop: asyncio.Event) -> None` in `rabbitmq.py`.

rstream facts checked against version 1.1.0:
- `SuperStreamProducer(host, port, username=, password=, super_stream=, super_stream_creation_option=SuperStreamCreationOption(n_partitions=N), routing_extractor=<async fn(message) -> str>, routing=RouteType.Hash)`.
- `start()` declares the super stream (exists is ok) and connects. `close()` closes.
- `send(message, on_publish_confirm=<async fn(ConfirmationStatus)>)`; the callback is per message, so a closure carries the per-message state (no shared dict, no lock).
- `ConfirmationStatus.is_confirmed` is the success flag.

- [ ] **Step 1: Implement `py-producer/rabbitmq.py`**

```python
import asyncio
import logging
import time
import uuid

from rstream import (
    AMQPMessage,
    ConfirmationStatus,
    RouteType,
    SuperStreamCreationOption,
    SuperStreamProducer,
)

import metrics
from env import get_env
from message import encode, new_message
from pacing import MAX_IN_FLIGHT, Pacer

BROKER = "rabbitmq"


async def routing_key(message: AMQPMessage) -> str:
    # The hash of this value picks the partition. Must be async for rstream.
    return message.application_properties["routing-key"]


async def run(stop: asyncio.Event) -> None:
    host = get_env("BROKER_HOST", "rabbitmq")
    port = int(get_env("BROKER_PORT", "5552"))
    user = get_env("BROKER_USER", "guest")
    password = get_env("BROKER_PASS", "guest")
    super_stream = get_env("TOPIC", "benchmark")
    partitions = int(get_env("PARTITIONS", "8"))
    rate_limit = int(get_env("RATE_LIMIT", "0"))
    msg_size = int(get_env("MSG_SIZE_BYTES", "256"))
    producer_id = get_env("PRODUCER_ID", uuid.uuid4().hex[:8])

    produced = metrics.messages_produced.labels(BROKER, producer_id)
    produced_bytes = metrics.bytes_produced.labels(BROKER, producer_id)
    errors = metrics.produce_errors.labels(BROKER, producer_id)
    ack_latency = metrics.produce_ack_latency.labels(BROKER, producer_id)

    producer = SuperStreamProducer(
        host,
        port,
        username=user,
        password=password,
        super_stream=super_stream,
        super_stream_creation_option=SuperStreamCreationOption(n_partitions=partitions),
        routing_extractor=routing_key,
        routing=RouteType.Hash,
    )

    # The client does not retry the first connect, so loop until the broker is up.
    while not stop.is_set():
        try:
            await producer.start()
            break
        except Exception as e:
            logging.warning("rabbitmq: connect failed (%s), retrying in 3s", e)
            await asyncio.sleep(3)
    if stop.is_set():
        return

    logging.info(
        "rabbitmq producer: id=%s superStream=%s partitions=%d rateLimit=%d msgSize=%d",
        producer_id, super_stream, partitions, rate_limit, msg_size,
    )

    in_flight = asyncio.Semaphore(MAX_IN_FLIGHT)
    pacer = Pacer(rate_limit)

    def make_confirm(send_time: float, size: int):
        # Closure: remembers this message's send time and size. This replaces the
        # Go sendTimes/msgBytes maps and their mutex: rstream calls this once per
        # message, so no shared state is touched.
        async def on_confirm(status: ConfirmationStatus) -> None:
            in_flight.release()
            if status.is_confirmed:
                ack_latency.observe(time.monotonic() - send_time)
                produced_bytes.inc(size)
                produced.inc()
            else:
                errors.inc()

        return on_confirm

    try:
        while not stop.is_set():
            await pacer.wait()
            await in_flight.acquire()
            msg_id = str(uuid.uuid4())
            data = encode(new_message(msg_id, msg_size))
            msg = AMQPMessage(body=data, application_properties={"routing-key": msg_id})
            send_time = time.monotonic()
            try:
                await producer.send(msg, on_publish_confirm=make_confirm(send_time, len(data)))
            except Exception as e:
                logging.error("rabbitmq: send: %s", e)
                errors.inc()
                in_flight.release()
    finally:
        await producer.close()
```

- [ ] **Step 2: Syntax check**

Run: `cd py-producer && .venv/bin/python -c "import rabbitmq"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add py-producer/rabbitmq.py
git commit -m "feat: python rabbitmq super-stream producer"
```

---

## Task 6: Producer main and Dockerfile

**Files:**
- Create: `py-producer/main.py`, `py-producer/Dockerfile`

**Interfaces:**
- Consumes: `redpanda.run`, `rabbitmq.run` (`async def run(stop: asyncio.Event)`), `metrics.serve_metrics`, `env.get_env`.

asyncio notes:
- `asyncio.run(main())` starts the event loop and runs `main()` as the first task. It is the closest thing to Go's `main`.
- `loop.add_signal_handler(sig, stop.set)` replaces `signal.Notify` plus the goroutine. `stop` is the `ctx.Done()` equivalent: every loop checks `stop.is_set()`.

- [ ] **Step 1: Implement `py-producer/main.py`**

```python
import asyncio
import logging
import signal
import sys

import metrics
import rabbitmq
import redpanda
from env import get_env


async def main() -> int:
    broker_type = get_env("BROKER_TYPE", "redpanda")
    if broker_type in ("redpanda", "kafka"):
        run = redpanda.run
    elif broker_type == "rabbitmq":
        run = rabbitmq.run
    else:
        logging.error("unknown BROKER_TYPE=%r, expected redpanda or rabbitmq", broker_type)
        return 1

    metrics.serve_metrics(get_env("METRICS_ADDR", ":2112"))

    # `stop` is our ctx.Done(): signal handlers set it, loops check it.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await run(stop)
    logging.info("producer exited")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Implement `py-producer/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so code edits do not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

EXPOSE 2112

# -u: unbuffered output so `docker logs` is live.
ENTRYPOINT ["python", "-u", "main.py"]
```

- [ ] **Step 3: Verify the bad-config path and the image build**

Run: `cd py-producer && BROKER_TYPE=bogus .venv/bin/python main.py; echo "exit=$?"`
Expected: log line `unknown BROKER_TYPE='bogus', expected redpanda or rabbitmq` and `exit=1`.

Run: `docker build -q -t py-producer-test py-producer`
Expected: prints an image id, no error.

- [ ] **Step 4: Commit**

```bash
git add py-producer/main.py py-producer/Dockerfile
git commit -m "feat: python producer entrypoint and image"
```

---

## Task 7: Consumers (Redpanda, RabbitMQ), main, Dockerfile

**Files:**
- Create: `py-consumer/redpanda.py`, `py-consumer/rabbitmq.py`, `py-consumer/main.py`, `py-consumer/Dockerfile`

**Interfaces:**
- Consumes: `message.decode`, `message.latency_seconds`, `env.get_env`, `metrics.*` (consumer), `metrics.serve_metrics`.
- Produces: `async def run(stop: asyncio.Event) -> None` in both broker modules.

asyncio notes:
- Redpanda: `consumer.getmany(timeout_ms=500)` fetches whatever arrived from all assigned partitions and returns a dict `{TopicPartition: [records]}`, or `{}` after the timeout. One consumer object serves all its partitions concurrently over the network, while Python code handles records one at a time. The timeout lets the loop re-check `stop`, so shutdown needs no task cancellation.
- RabbitMQ: `rstream` opens one subscription per partition and calls our callback from its own tasks. `await stop.wait()` parks `run` until the signal handler fires, like `<-ctx.Done()`.
- The RabbitMQ callback is a plain function with no await in it, so two messages never interleave inside it.

rstream facts checked against version 1.1.0: `SuperStreamConsumer(host, port, username=, password=, super_stream=)`, `await subscribe(callback, decoder=amqp_decoder, offset_specification=ConsumerOffsetSpecification(OffsetType.NEXT, None), subscriber_name=, properties=, consumer_update_listener=)`. For SAC the `properties` dict needs `single-active-consumer`, `name`, and `super-stream`; the `super-stream` key is not referenced by the library, so it is confirmed only by the E2E run in Task 8.

- [ ] **Step 1: Implement `py-consumer/redpanda.py`**

```python
import asyncio
import logging
import uuid

from aiokafka import AIOKafkaConsumer

import metrics
from env import get_env
from message import decode, latency_seconds

BROKER = "redpanda"


async def run(stop: asyncio.Event) -> None:
    brokers = get_env("BROKER_ADDR", "redpanda:9092")
    topic = get_env("TOPIC", "benchmark")
    # Instances sharing a group each get a distinct subset of partitions.
    group = get_env("CONSUMER_GROUP", "benchmark-consumers")
    consumer_id = get_env("CONSUMER_ID", uuid.uuid4().hex[:8])

    consumed = metrics.messages_consumed.labels(BROKER, consumer_id)
    consumed_bytes = metrics.bytes_consumed.labels(BROKER, consumer_id)
    errors = metrics.consume_errors.labels(BROKER, consumer_id)
    e2e = metrics.e2e_latency.labels(BROKER)

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=brokers,
        group_id=group,
        # Start at the end so only messages produced during the run are measured.
        auto_offset_reset="latest",
    )
    await consumer.start()
    logging.info("redpanda consumer started: id=%s group=%s topic=%s", consumer_id, group, topic)
    try:
        while not stop.is_set():
            batches = await consumer.getmany(timeout_ms=500)
            for records in batches.values():
                for record in records:
                    try:
                        msg = decode(record.value)
                    except ValueError as e:
                        logging.warning("redpanda: decode error: %s", e)
                        errors.inc()
                        continue
                    consumed.inc()
                    consumed_bytes.inc(len(record.value))
                    e2e.observe(latency_seconds(msg))
    finally:
        await consumer.stop()
```

- [ ] **Step 2: Implement `py-consumer/rabbitmq.py`**

```python
import asyncio
import logging
import uuid

from rstream import (
    AMQPMessage,
    ConsumerOffsetSpecification,
    EventContext,
    MessageContext,
    OffsetSpecification,
    OffsetType,
    SuperStreamConsumer,
    amqp_decoder,
)

import metrics
from env import get_env
from message import decode, latency_seconds

BROKER = "rabbitmq"
CONSUMER_NAME = "benchmark-consumer"


async def run(stop: asyncio.Event) -> None:
    host = get_env("BROKER_HOST", "rabbitmq")
    port = int(get_env("BROKER_PORT", "5552"))
    user = get_env("BROKER_USER", "guest")
    password = get_env("BROKER_PASS", "guest")
    super_stream = get_env("TOPIC", "benchmark")
    consumer_id = get_env("CONSUMER_ID", uuid.uuid4().hex[:8])

    consumed = metrics.messages_consumed.labels(BROKER, consumer_id)
    consumed_bytes = metrics.bytes_consumed.labels(BROKER, consumer_id)
    errors = metrics.consume_errors.labels(BROKER, consumer_id)
    e2e = metrics.e2e_latency.labels(BROKER)

    def on_message(msg: AMQPMessage, _ctx: MessageContext) -> None:
        # No await in here, so this never interleaves with another message.
        raw = msg.body
        if not raw:
            return
        try:
            decoded = decode(raw)
        except ValueError as e:
            logging.warning("rabbitmq: decode: %s", e)
            errors.inc()
            return
        consumed.inc()
        consumed_bytes.inc(len(raw))
        e2e.observe(latency_seconds(decoded))

    async def on_consumer_update(_is_active: bool, _ctx: EventContext) -> OffsetSpecification:
        # Called when the broker makes this instance active or inactive for a
        # partition. Always continue from the end, like the Go consumer.
        return OffsetSpecification(OffsetType.NEXT, 0)

    consumer = SuperStreamConsumer(host, port, username=user, password=password, super_stream=super_stream)
    await consumer.start()
    try:
        # The client does not retry the first connect, so loop until the broker is up.
        # ponytail: a failure halfway through subscribing may double-subscribe on
        # retry, add a close/recreate if that is ever observed.
        while not stop.is_set():
            try:
                await consumer.subscribe(
                    callback=on_message,
                    decoder=amqp_decoder,
                    offset_specification=ConsumerOffsetSpecification(OffsetType.NEXT, None),
                    subscriber_name=CONSUMER_NAME,
                    # Single active consumer: instances sharing the name form a group
                    # and the broker makes exactly one active per partition.
                    properties={
                        "single-active-consumer": "true",
                        "name": CONSUMER_NAME,
                        "super-stream": super_stream,
                    },
                    consumer_update_listener=on_consumer_update,
                )
                break
            except Exception as e:
                logging.warning("rabbitmq: subscribe failed (%s), retrying in 3s", e)
                await asyncio.sleep(3)
        logging.info("rabbitmq consumer: id=%s superStream=%s", consumer_id, super_stream)
        await stop.wait()
    finally:
        await consumer.close()
```

- [ ] **Step 3: Implement `py-consumer/main.py`**

Same as `py-producer/main.py` with the log text changed:

```python
import asyncio
import logging
import signal
import sys

import metrics
import rabbitmq
import redpanda
from env import get_env


async def main() -> int:
    broker_type = get_env("BROKER_TYPE", "redpanda")
    if broker_type in ("redpanda", "kafka"):
        run = redpanda.run
    elif broker_type == "rabbitmq":
        run = rabbitmq.run
    else:
        logging.error("unknown BROKER_TYPE=%r, expected redpanda or rabbitmq", broker_type)
        return 1

    metrics.serve_metrics(get_env("METRICS_ADDR", ":2112"))

    # `stop` is our ctx.Done(): signal handlers set it, loops check it.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await run(stop)
    logging.info("consumer exited")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Implement `py-consumer/Dockerfile`**

Identical to `py-producer/Dockerfile`.

- [ ] **Step 5: Verify imports, bad-config path and image build**

Run:

```bash
cd py-consumer && .venv/bin/python -c "import redpanda, rabbitmq" && BROKER_TYPE=bogus .venv/bin/python main.py; echo "exit=$?"; cd .. && docker build -q -t py-consumer-test py-consumer
```

Expected: `exit=1` after the `unknown BROKER_TYPE` log line, and an image id from the build.

- [ ] **Step 6: Run all unit tests**

Run: `for d in py-producer py-consumer; do (cd $d && .venv/bin/python -m unittest discover -s tests); done`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add py-consumer
git commit -m "feat: python redpanda and rabbitmq consumers"
```

---

## Task 8: Compose overrides and end-to-end run

**Files:**
- Create: `docker-compose-redpanda-py.yml`, `docker-compose-rabbitmq-py.yml`
- Modify: `docs/superpowers/specs/2026-10-12-python-async-producer-consumer-design.md` (align to what landed, append observed results)

A compose override may only mention services its base file defines, so there is one override per broker, not one file (this replaces the single-file idea in the spec).

- [ ] **Step 1: Write `docker-compose-redpanda-py.yml`**

```yaml
# Swaps the Go producer/consumer images for the Python ones.
# Usage: docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml up --build
services:
  redpanda-producer:
    build:
      context: ./py-producer
  redpanda-consumer:
    build:
      context: ./py-consumer
```

- [ ] **Step 2: Write `docker-compose-rabbitmq-py.yml`**

```yaml
# Swaps the Go producer/consumer images for the Python ones.
# Usage: docker compose -f docker-compose-rabbitmq.yml -f docker-compose-rabbitmq-py.yml up --build
services:
  rabbitmq-producer:
    build:
      context: ./py-producer
  rabbitmq-consumer:
    build:
      context: ./py-consumer
```

- [ ] **Step 3: Check the merged config**

Run: `docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml config | grep -A3 "build:"`
Expected: both services show `context` ending in `py-producer` / `py-consumer`.
Run the same for RabbitMQ.

- [ ] **Step 4: E2E Redpanda**

```bash
docker compose -f docker-compose-monitoring.yml up -d
docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml up -d --build
sleep 30
docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml logs redpanda-producer redpanda-consumer | tail -20
docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml exec redpanda-producer python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:2112/metrics').read().decode())" | grep -E "^benchmark_(messages|bytes)_produced_total"
docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml exec redpanda-consumer python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:2112/metrics').read().decode())" | grep -E "^benchmark_(messages_consumed_total|e2e_latency_seconds_count)"
```

Expected: producer and consumer logs show started lines and no tracebacks. Produced and consumed counters are both greater than 0, consumed trails produced.
Then open Grafana (`http://localhost:3000`) and confirm throughput, ack latency and E2E latency panels populate.
Then: `docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml stop` and confirm containers exit with code 0 (`docker compose ps -a`) and logs show `producer exited` / `consumer exited`.

- [ ] **Step 5: E2E Redpanda scaled and mixed**

```bash
docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml up -d --scale redpanda-producer=2 --scale redpanda-consumer=2
```

Expected: after about 30s, two `consumer_id` series appear in Grafana and partitions are split.
Mixed languages: run once with only the base file (Go producer and consumer), then `up -d --build redpanda-consumer` with the override to replace only the consumer. Messages from the Go producer must decode in the Python consumer (`consume_errors` stays 0, E2E latency sane). Repeat with the roles reversed.

- [ ] **Step 6: E2E RabbitMQ**

Repeat Steps 4 and 5 with `docker-compose-rabbitmq.yml` and `docker-compose-rabbitmq-py.yml`, service names `rabbitmq-producer` / `rabbitmq-consumer`.
Extra checks: the RabbitMQ management UI (or `docker compose exec rabbitmq rabbitmq-streams list_stream_consumers`) shows the consumers as single active per partition with two instances. If SAC fails to register, the `super-stream` property key in `py-consumer/rabbitmq.py` is the first suspect: record the actual outcome in the spec instead of working around it silently.

- [ ] **Step 7: Review Focus checks**

- `RATE_LIMIT=0`: run `docker stats --no-stream` on the producer for 60s and confirm memory stays under the 256M limit (in-flight semaphore holds).
- `MSG_SIZE_BYTES=0 docker compose ... up -d`: producer and consumer run, `consume_errors` stays 0.
- Stop the broker (`docker compose stop redpanda` or `rabbitmq`) while the producer runs, then start it again: confirm no crash loop beyond one restart and that production resumes.

- [ ] **Step 8: Align the spec and record results**

Edit the spec in place, bump `updated:`:
- Compose section: two override files, one per broker, with the reason above.
- RabbitMQ producer section: closures replace the maps (no shared state at all), plus the in-flight semaphore and `pacing.py` module.
- Layout block: add `pacing.py`, `env.py`, `tests/`.
- Strike through resolved Open Questions with the one-line answer (`rstream` API confirmed at 1.1.0, SAC `super-stream` property outcome from Step 6).
- Append an "Observed results" subsection under E2E / Manual QA with what actually happened in Steps 4 to 7, including anything unexpected. Set `status: implemented` only when every step passed.

- [ ] **Step 9: Commit**

```bash
git add docker-compose-redpanda-py.yml docker-compose-rabbitmq-py.yml docs
git commit -m "feat: compose overrides for python images and e2e results"
```
