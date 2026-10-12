---
status: implemented
date: 2026-10-12
updated: 2026-10-12
issues: []
scope: [producer, consumer, python, compose]
---

# Python asyncio producer and consumer

## Problem

The PoC benchmark has Go producers and consumers only.
There is no Python equivalent to learn from or compare against.
The author reads Go and C++ fluently (goroutines, channels, mutexes, worker pools) but is new to Python and to `asyncio`.
So the port also has to teach: the code should make the Go-to-asyncio mapping obvious.

## Goals

- `py-producer/` and `py-consumer/` behave like `producer/` and `consumer/` for both `BROKER_TYPE=redpanda` and `BROKER_TYPE=rabbitmq`.
- Same env vars, same JSON message (`id`, `publish_time_ns`, `payload`), same metric names, labels and histogram buckets on port 2112.
- The existing Grafana dashboard and Prometheus config work unchanged with the Python images.
- Two small compose override files (one per broker) swap the Python images in for the producer and consumer services.
- Code comments explain each asyncio construct by naming its Go or C++ counterpart.
- A message encoded by Go decodes in Python and the reverse.

## Non-Goals

- No performance tuning (`uvloop`, multi-process, threads).
- No changes to the Go code, broker compose files, Prometheus or Grafana.
- No Flink restoration.
- No in-process consumer worker pool.
  One process consumes whatever the broker assigns it, as the Go consumer does today.

## Architecture / Design

### Layout

Mirrors the Go modules, one directory per role, each with its own `Dockerfile` (`python:3.12-slim`) and `requirements.txt`.

```
py-producer/  main.py  env.py  message.py  metrics.py  pacing.py  redpanda.py  rabbitmq.py  tests/
py-consumer/  main.py  env.py  message.py  metrics.py  redpanda.py  rabbitmq.py  tests/
docker-compose-redpanda-py.yml
docker-compose-rabbitmq-py.yml
```

Libraries: `aiokafka` (Redpanda), `rstream` (RabbitMQ streams and super-streams), `prometheus_client` (metrics).
`prometheus_client.start_http_server(2112)` serves `/metrics` from a background thread, the same role as `go serveMetrics`.

### Compose

`docker-compose-redpanda-py.yml` and `docker-compose-rabbitmq-py.yml` override only `build.context` for that broker's producer and consumer.
There is one file per broker because a compose override may only mention services its base file defines.
Usage: `docker compose -f docker-compose-redpanda.yml -f docker-compose-redpanda-py.yml up --build`.
Service names are unchanged, so Prometheus DNS discovery and the dashboard labels keep working.
Each `py-*` directory has a `.dockerignore` (`.venv/`, `__pycache__/`, `tests/`) to keep the build context small.

### asyncio for a Go and C++ reader

The rule that explains most surprises: **asyncio is one thread running one event loop.**
Tasks are not goroutines in the scheduling sense.
The Go runtime preempts goroutines and spreads them over cores.
asyncio switches tasks only where code says `await`.
Between two `await`s a task runs alone and uninterrupted.

| Go / C++ | asyncio | Notes |
|---|---|---|
| `go f()` | `asyncio.create_task(f())` | `f` must be an `async def`. The task only makes progress when something awaits. |
| blocking call that parks the goroutine | `await x` | The task is suspended and the loop runs other tasks. This is the only switch point. |
| `ctx.Done()` / `cancel()` | `asyncio.Event` set by a signal handler, checked in loops | `task.cancel()` also exists, it raises `CancelledError` at the task's current `await`. |
| `chan T` | `asyncio.Queue` | `await q.get()` parks like `<-ch`. `put_nowait` and `get_nowait` do not park. |
| `time.Ticker` | schedule against `time.monotonic()` and `await asyncio.sleep(delta)` | `sleep` is the yield point. |
| `sync.Mutex`, `std::mutex` | usually nothing | No two tasks run between awaits, so a dict update with no `await` inside is already atomic. A lock (`asyncio.Lock`) is only needed if the critical section contains an `await`. |
| worker pool | N tasks reading one `asyncio.Queue` | Gives concurrency of waiting (IO), not parallel CPU work. |
| `select` | `asyncio.wait(..., return_when=FIRST_COMPLETED)` | Not needed in this port. |

Three traps, each called out in comments where it bites:

1. **Blocking the loop.**
   A call that does not `await` and takes time (`time.sleep`, a sync network call, heavy CPU) freezes every task, including ack handling and shutdown.
2. **Starvation with `RATE_LIMIT=0`.**
   A producer loop with no real `await` never lets ack callbacks run.
   The loop always passes through an awaited send or `await asyncio.sleep(0)` so other tasks get a turn.
3. **No parallelism.**
   One process uses roughly one core for Python code, regardless of task count.
   Scaling stays container-level (`--scale`), as for Go.

### Control flow

`main.py` mirrors Go `main`: read `BROKER_TYPE` and `METRICS_ADDR`, start metrics, install SIGINT/SIGTERM handlers that set a `stop` event, then `asyncio.run(run(stop))` for the chosen broker.
Each broker module exposes `async def run(stop: asyncio.Event)`, the counterpart of `run(ctx)`.

### Producer

Common loop, per message: optional rate-limit wait, build and JSON-encode `Message`, send, record metrics on ack.
Rate limiting uses a monotonic schedule (`next += 1/rate`, then `await asyncio.sleep(max(0, next - now))`) so drift does not accumulate.

- **Redpanda:** `AIOKafkaProducer`, key = message id.
  `await producer.send(topic, value, key)` returns once the record is queued and gives a future for the ack.
  A done-callback on that future records `messages_produced`, `bytes_produced` and `produce_ack_latency`, or `produce_errors`.
  Topic creation via the admin client with `PARTITIONS`, tolerating "already exists".
  Metric `producer_id` label from `PRODUCER_ID` or an 8-char uuid, as in Go.
- **RabbitMQ:** `rstream` super-stream producer with a hash routing key taken from `id`, publish-confirm callback for metrics.
  The Go `sendTimes` and `msgBytes` maps guarded by a mutex are replaced by per-message closures, so there is no shared state at all.
  An in-flight semaphore (`MAX_IN_FLIGHT`) bounds unconfirmed messages, which keeps memory flat at `RATE_LIMIT=0`.
  Pacing lives in the `pacing.py` module (injectable clock), shared by both producers.
  Declare the super stream with `PARTITIONS` partitions.
  Retry the initial connect in a loop (`await asyncio.sleep(3)`), as the Go client needs.

### Consumer

- **Redpanda:** `AIOKafkaConsumer` in group `CONSUMER_GROUP`, offset reset to latest, `async for record in consumer`.
  Per record: decode JSON, increment `messages_consumed` and `bytes_consumed`, observe `e2e_latency` with `time.time_ns() - publish_time_ns`.
  Decode errors increment `consume_errors`.
- **RabbitMQ:** `rstream` super-stream consumer with consumer name `benchmark-consumer`, offset next, single active consumer, handler doing the same per-message work.
  Then `await stop.wait()` until shutdown, like `<-ctx.Done()`.

### Message

`message.py` exposes `new_message(id, size)`, `encode(msg) -> bytes` and `decode(bytes) -> Message` with the exact JSON keys of the Go struct.
Payload is `size` NUL bytes, as in Go (`string(make([]byte, n))`).
Compact JSON separators so byte counts match Go closely.

## Validation

- A small stdlib `unittest` file per role covering `message.py`: round-trip, key names, payload length, and decoding a fixed JSON sample produced by the Go encoder.
- A rate-limit unit check using an injected fake clock to assert the schedule does not drift.
- Broker code is validated end to end (below), not mocked.

## E2E / Manual QA

For each broker: start the stack with the override, then check:

| Check | Expected |
|---|---|
| `curl :2112/metrics` on producer and consumer | all metric names from `CLAUDE.md` present |
| produced and consumed counters | both increase, consumed trails produced |
| Grafana dashboard | throughput, ack latency and E2E latency panels populate, no config changes |
| `--scale ...=4` | consumers split partitions, per-`consumer_id` series appear |
| SIGTERM | clean exit, no traceback |
| Go producer with Python consumer, and the reverse | messages decode, latency is sane |

### Observed results

Run on 2026-10-12 (WSL2, default `.env`: `PARTITIONS=4`, broker 2 CPU, producer and consumer 0.5 CPU / 256M).
Grafana was not opened in a browser.
Its panel queries were run against Prometheus (`localhost:9090`) instead, and all three families (throughput, ack latency p99, E2E latency p99) returned data for both brokers.

- **Redpanda, Python producer and consumer:** started cleanly, no tracebacks, produced and consumed counters both grow, `consume_errors` and `produce_errors` stay 0.
- **Redpanda scaled (2 and 2):** two `consumer_id` series, each about 10.2k msg/s, partitions split 2 and 2 (`Setting newly assigned partitions` in each consumer log).
- **RabbitMQ, Python producer and consumer:** same, `consume_errors` 0.
  `rabbitmq-streams list_stream_consumers` shows `single-active-consumer: true` and `super-stream: benchmark` properties.
  With two consumers each partition has exactly one `single_active` and one `waiting` subscription, split 2 and 2 across the two connections.
  So the `super-stream` property key is accepted and SAC works.
- **Mixed languages:** Go producer with Python consumer and Python producer with Go consumer both decode with `consume_errors` 0, for both brokers.
  Redpanda E2E averaged about 1 ms (Go producer, Python consumer) and 0.6 ms (Python producer, Go consumer) at `RATE_LIMIT=1000`.
  RabbitMQ E2E averaged about 53 ms (Go producer, Python consumer) and about 258 ms (Python producer, Go consumer).
- **SIGTERM:** `docker compose stop` exits every Python container with code 0 in under 2 s and logs `producer exited` / `consumer exited`.
  Checked at `RATE_LIMIT=0` and `RATE_LIMIT=10` for both brokers, and at 2 and 2 instances.
- **Memory at `RATE_LIMIT=0`:** producers stay around 32 MiB (Redpanda) and 57-60 MiB (RabbitMQ), consumers 26-31 MiB, all far below 256M.
- **`MSG_SIZE_BYTES=0`:** producer and consumer run on both brokers, `consume_errors` 0 (about 96 bytes per message, the JSON envelope).
- **Broker down and up (`RATE_LIMIT=1000`):** no container restarts (`RestartCount` 0) and production and consumption resume after the broker returns.
  Redpanda: `aiokafka` retries internally and logs about ten `NodeNotReadyError` lines per second while the broker is down.
  RabbitMQ: while the broker container is stopped its DNS name does not resolve, so `rstream` logs `gaierror` tracebacks from its recovery loop at 10 s intervals.
  It reconnected on its own after the broker was back, about 990 msg/s on both sides.

Throughput and ack latency at `RATE_LIMIT=0`, one producer, measured over 30 s (no tuning):

| Broker | Produced msg/s | Mean ack latency |
|---|---|---|
| Redpanda (`aiokafka`) | 11.6k | 2.4 ms |
| RabbitMQ (`rstream`) | 8.5k | 816 ms |

`rstream` flushes buffered publishes on a 0.5 s timer, so the RabbitMQ ack latency (and the E2E latency with a Python producer, about 258 ms at 1000 msg/s) is dominated by that interval, not by the broker.
Treat Python-producer RabbitMQ latency figures as bounded below by it.

Bug found and fixed during the run: the Go producer (franz-go) compresses batches with snappy by default, and the Python consumer crashed with `UnsupportedCodecError` (crash loop) when reading them.
`python-snappy` was added to `py-consumer/requirements.txt`.
The Python producer sends uncompressed batches, so the producer image does not need it.

## Open Questions

- ~~Exact `rstream` super-stream and SAC API at the pinned version.~~ Confirmed at `rstream` 1.1.0. The `super-stream` consumer property registers single active consumers per partition, verified with `rabbitmq-streams list_stream_consumers` (see Observed results).
- `aiokafka` partitioner (not revisited, unchanged): its default murmur2 key partitioner differs from franz-go's sticky key partitioner. Partition placement may differ between Go and Python producers, throughput comparison is unaffected.

## Out of Scope

- `uvloop`, multi-process workers, and a task-per-partition consumer variant.
- Running the testplan suite against the Python images and a Python vs Go comparison report.

## References

- `producer/` and `consumer/` (Go reference implementation)
- `CLAUDE.md` (metric table, env vars, RabbitMQ client notes)
- `docker-compose-redpanda.yml`, `docker-compose-rabbitmq.yml`, `prometheus/prometheus.yml`
