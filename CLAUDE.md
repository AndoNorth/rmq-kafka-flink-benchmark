# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repo contains two benchmarking stacks:

1. **Legacy stack** — compares RabbitMQ SuperStream and Kafka using Python producers and Apache Flink consumers (`kk-rmq-flink-compose.yaml`, `flink-jobs/`, `kafka-producer/`, `rabbitmq-producer/`).

2. **PoC benchmark** — compares Redpanda (Kafka-compatible) and RabbitMQ SuperStreams using Go producers and consumers with Prometheus/Grafana metrics (`docker-compose-redpanda.yml`, `docker-compose-rabbitmq.yml`, `docker-compose-monitoring.yml`, `producer/`, `consumer/`).

## Key project conventions

- Use `docker-compose` (not `docker compose`) — the machine uses the standalone CLI backed by Docker Compose v2 via Colima.
- The Docker socket is at `~/.colima/default/docker.sock` (not `/var/run/docker.sock`). Do NOT rely on Docker SD in Prometheus; use DNS SD instead.
- Go modules live in `producer/` and `consumer/` with separate `go.mod` files. Run `go mod tidy` inside each directory to resolve dependencies.
- The Dockerfile runs `go mod tidy` at build time (no pre-committed `go.sum`).

## PoC benchmark architecture

```
docker-compose-monitoring.yml   → creates "benchmark" Docker network + Prometheus + Grafana
docker-compose-redpanda.yml     → Redpanda broker + redpanda-producer + redpanda-consumer
docker-compose-rabbitmq.yml     → RabbitMQ broker + rabbitmq-producer + rabbitmq-consumer
```

Services are named `{broker}-producer` / `{broker}-consumer` so Prometheus DNS SD can label metrics by broker without needing the Docker socket.

Scaling:
```bash
docker-compose -f docker-compose-redpanda.yml up --scale redpanda-producer=4 --scale redpanda-consumer=4
docker-compose -f docker-compose-rabbitmq.yml up --scale rabbitmq-producer=4 --scale rabbitmq-consumer=4
```

## Go library versions (PoC benchmark)

- `github.com/twmb/franz-go v1.18.0` — Kafka/Redpanda client
- `github.com/twmb/franz-go/pkg/kadm v1.14.0` — Kafka admin (topic creation)
- `github.com/rabbitmq/rabbitmq-stream-go-client v1.4.4` — RabbitMQ streams
- `github.com/prometheus/client_golang v1.20.5` — metrics
- `github.com/google/uuid v1.6.0`

## RabbitMQ stream client API notes (v1.4.4)

- Routing strategy: `stream.NewHashRoutingStrategy(func(msg message.StreamMessage) string { ... })` — NOT `HashRoutingMurmur3Strategy`.
- Producer options: `stream.NewSuperStreamProducerOptions(routingStrategy)`.
- Confirmation channel: `producer.NotifyPublishConfirmation(bufferSize int)` returns `chan PartitionPublishConfirm`. Iterate `partConfirm.ConfirmationStatus` (a `[]*ConfirmationStatus` slice) to get per-message status.
- Consumer SAC: use `stream.NewSingleActiveConsumer(consumerUpdateFunc)` passed to `opts.SetSingleActiveConsumer(sac)`.
- Connection retry: the client does NOT retry on first connect — wrap `stream.NewEnvironment(...)` in a retry loop.

## Metrics exposed (port 2112 on each producer/consumer)

| Metric | Type | Labels |
|---|---|---|
| `benchmark_messages_produced_total` | counter | broker, producer_id |
| `benchmark_bytes_produced_total` | counter | broker, producer_id |
| `benchmark_produce_errors_total` | counter | broker, producer_id |
| `benchmark_produce_ack_latency_seconds` | histogram | broker, producer_id |
| `benchmark_messages_consumed_total` | counter | broker, consumer_id |
| `benchmark_bytes_consumed_total` | counter | broker, consumer_id |
| `benchmark_consume_errors_total` | counter | broker, consumer_id |
| `benchmark_e2e_latency_seconds` | histogram | broker |

## Environment variables (.env)

| Variable | Default | Purpose |
|---|---|---|
| `PARTITIONS` | `8` | Partition count for topic/super-stream |
| `RATE_LIMIT` | `0` | Messages/sec per producer (0 = unlimited) |
| `MSG_SIZE_BYTES` | `256` | Payload bytes per message |
| `TOPIC` | `benchmark` | Topic / super-stream name |
| `BROKER_CPUS` | `2.0` | CPU limit for broker container |
| `BROKER_MEMORY` | `2G` | Memory limit for broker container |
| `PRODUCER_CPUS` | `0.5` | CPU limit per producer instance |
| `PRODUCER_MEMORY` | `256M` | Memory limit per producer instance |
| `CONSUMER_CPUS` | `0.5` | CPU limit per consumer instance |
| `CONSUMER_MEMORY` | `256M` | Memory limit per consumer instance |
