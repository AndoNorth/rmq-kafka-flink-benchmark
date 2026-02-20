# Redpanda vs RabbitMQ SuperStream — Benchmark PoC

A head-to-head comparison of **Redpanda** (Kafka-compatible) and **RabbitMQ SuperStreams** as message brokers, using identical Go producers and consumers running inside Docker Compose with equal CPU/memory allocations.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  docker-compose-monitoring.yml                              │
│  ┌────────────┐   ┌──────────┐                             │
│  │ Prometheus │   │ Grafana  │  ← http://localhost:3000    │
│  └──────┬─────┘   └──────────┘                             │
│         │ scrapes /metrics:2112 via Docker SD              │
└─────────┼───────────────────────────────────────────────────┘
          │  (shared "benchmark" Docker network)
┌─────────┴─────────────────────────────────────────────────────┐
│  docker-compose-redpanda.yml  OR  docker-compose-rabbitmq.yml │
│                                                               │
│  ┌──────────┐   ┌─────────┐   ┌──────────┐                  │
│  │ Producer │──▶│  Broker │──▶│ Consumer │                  │
│  │ (×N)     │   │         │   │ (×M)     │                  │
│  └──────────┘   └─────────┘   └──────────┘                  │
└───────────────────────────────────────────────────────────────┘
```

**Both stacks share the same:**
- Go producer and consumer binaries (controlled by `BROKER_TYPE` env var)
- Prometheus metrics format and labels
- Resource limits (CPU / memory) per container
- `benchmark` Docker network (so Prometheus can scrape both)

## Metrics collected

| Metric | Labels | Description |
|---|---|---|
| `benchmark_messages_produced_total` | broker, producer_id | Messages successfully acknowledged by the broker |
| `benchmark_bytes_produced_total` | broker, producer_id | Payload bytes produced |
| `benchmark_produce_errors_total` | broker, producer_id | Produce failures |
| `benchmark_produce_ack_latency_seconds` | broker, producer_id | Histogram: send → broker ack |
| `benchmark_messages_consumed_total` | broker, consumer_id | Messages consumed |
| `benchmark_bytes_consumed_total` | broker, consumer_id | Payload bytes consumed |
| `benchmark_consume_errors_total` | broker, consumer_id | Consume / decode failures |
| `benchmark_e2e_latency_seconds` | broker | **Histogram: publish_time_ns → consumer receive** |

Derived Grafana panels compute:
- **Producer throughput** — `rate(messages_produced_total[30s])` summed across all instances
- **Consumer throughput** — `rate(messages_consumed_total[30s])` summed across all instances
- **E2E latency p50 / p95 / p99** — `histogram_quantile` over `e2e_latency_seconds`

## Prerequisites

- Docker ≥ 24 with the Compose v2 plugin
- ~6 GB of free RAM (broker + producers + consumers + monitoring)
- Internet access for the first `docker compose build` (downloads Go modules)

## Quick start

### 1. Start the monitoring stack

The monitoring stack creates the shared Docker network. Start it first, then start cAdvisor (auto-selects the correct variant for your Docker storage driver).

```bash
docker compose -f docker-compose-monitoring.yml up -d

# Standard Linux (overlay2 driver):
docker compose -f docker-compose-cadvisor-standard.yml up -d

# WSL2 / Docker with containerd snapshotter (overlayfs driver):
docker compose -f docker-compose-cadvisor-wsl2.yml up -d
```

Check which driver you have: `docker info --format '{{.Driver}}'`

Open Grafana: http://localhost:3000 (credentials: `admin` / `admin`)
Open Prometheus: http://localhost:9090

- check health of metrics - http://localhost:9090/targets

### 2. Run the Redpanda benchmark

```bash
docker-compose -f docker-compose-redpanda.yml up --build
```

### 3. Run the RabbitMQ benchmark

Stop Redpanda first, then:

```bash
docker-compose -f docker-compose-redpanda.yml down
docker-compose -f docker-compose-rabbitmq.yml up --build
```

> **Note:** Run one broker stack at a time so resource measurements are not skewed.
> Grafana retains historical data, so you can compare runs side-by-side after both have completed.

## Scaling producers and consumers

Edit `.env` or pass variables on the command line.
Each stack uses prefixed service names so Prometheus can label metrics by broker:

```bash
# Redpanda — 4 producers, 4 consumers
docker-compose -f docker-compose-redpanda.yml up --build \
  --scale redpanda-producer=4 \
  --scale redpanda-consumer=4

# RabbitMQ — 4 producers, 4 consumers
docker-compose -f docker-compose-rabbitmq.yml up --build \
  --scale rabbitmq-producer=4 \
  --scale rabbitmq-consumer=4
```

Each producer and consumer instance gets its own `producer_id` / `consumer_id` label.
Grafana panels aggregate across all instances with `sum by (broker)`.

### Scaling effect on metrics

| Scaling scenario | Expected observation |
|---|---|
| More producers, same consumers | Producer throughput ↑, E2E latency may ↑ as broker saturates |
| Same producers, more consumers | Consumer throughput ↑ (partitions shared across consumers), E2E latency ↓ |
| Symmetric scale-out | Both throughputs ↑; latency depends on broker saturation point |

## Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `PARTITIONS` | `8` | Partition count (Redpanda topic / RabbitMQ super stream) |
| `PRODUCER_SCALE` | `1` | Default `--scale producer=N` passed to compose |
| `CONSUMER_SCALE` | `1` | Default `--scale consumer=N` |
| `RATE_LIMIT` | `0` | Messages/sec per producer instance (0 = unlimited) |
| `MSG_SIZE_BYTES` | `256` | Payload bytes appended to each message |
| `TOPIC` | `benchmark` | Topic / super-stream name |
| `BROKER_CPUS` | `2.0` | CPU limit for the broker container |
| `BROKER_MEMORY` | `2G` | Memory limit for the broker container |
| `PRODUCER_CPUS` | `0.5` | CPU limit per producer instance |
| `PRODUCER_MEMORY` | `256M` | Memory limit per producer instance |
| `CONSUMER_CPUS` | `0.5` | CPU limit per consumer instance |
| `CONSUMER_MEMORY` | `256M` | Memory limit per consumer instance |

## Resource parity

Both compose files apply **identical** resource limits to their broker, producer, and consumer containers.
Redpanda is configured with `--smp 2 --memory 1800M` to match the 2-CPU / 2 GB container ceiling.
RabbitMQ runs with the same container-level limits applied via `deploy.resources`.

## Useful commands

```bash
# Tail producer logs
docker-compose -f docker-compose-redpanda.yml logs -f redpanda-producer

# Check Redpanda topic stats
docker-compose -f docker-compose-redpanda.yml exec redpanda \
  rpk topic describe benchmark

# Check RabbitMQ super stream connections
docker-compose -f docker-compose-rabbitmq.yml exec rabbitmq \
  rabbitmq-streams list_stream_connections

# Open RabbitMQ management UI
open http://localhost:15672   # guest / guest

# Reload Prometheus config without restart
curl -X POST http://localhost:9090/-/reload

# Stop everything
docker-compose -f docker-compose-redpanda.yml down
docker-compose -f docker-compose-rabbitmq.yml down
docker-compose -f docker-compose-cadvisor-standard.yml down   # or cadvisor-wsl2
docker-compose -f docker-compose-monitoring.yml down
```

## Project layout

```
.
├── producer/                   Go producer (Redpanda + RabbitMQ)
│   ├── main.go                 Entry point — reads BROKER_TYPE
│   ├── redpanda.go             Redpanda/Kafka producer (franz-go)
│   ├── rabbitmq.go             RabbitMQ SuperStream producer
│   ├── message.go              Shared message struct with publish timestamp
│   ├── metrics.go              Prometheus counters / histograms
│   ├── go.mod
│   └── Dockerfile
├── consumer/                   Go consumer (Redpanda + RabbitMQ)
│   ├── main.go
│   ├── redpanda.go             Redpanda consumer (franz-go consumer groups)
│   ├── rabbitmq.go             RabbitMQ SuperStream consumer (SAC)
│   ├── message.go
│   ├── metrics.go
│   ├── go.mod
│   └── Dockerfile
├── prometheus/
│   └── prometheus.yml          Docker SD config — auto-discovers containers
├── grafana/
│   ├── provisioning/           Auto-provisioned datasource + dashboard
│   └── dashboards/
│       └── benchmark.json      Pre-built dashboard (throughput, latency, errors)
├── rabbitmq/
│   └── enabled_plugins         Enables rabbitmq_stream + rabbitmq_stream_management
├── docker-compose-redpanda.yml
├── docker-compose-rabbitmq.yml
├── docker-compose-monitoring.yml
├── docker-compose-cadvisor-standard.yml  cAdvisor for overlay2 (standard Linux)
├── docker-compose-cadvisor-wsl2.yml      cAdvisor for overlayfs (WSL2 / containerd snapshotter)
├── testplan/                   Automated test suite
│   ├── testplan.json           Default test plan
│   ├── run-benchmark-suite.sh  Runner (auto-selects cAdvisor variant)
│   └── README.md
└── .env                        Shared configuration
```

## How E2E latency is measured

Each message carries a `publish_time_ns` field set by the producer at the moment `Send()` is called (Unix nanoseconds).
The consumer records `time.Now().UnixNano()` upon receipt and computes:

```
latency = (now_ns - publish_time_ns) / 1e9   [seconds]
```

Because both producer and consumer containers share the same Docker host clock (`/dev/rtc`), no NTP synchronisation is needed.
