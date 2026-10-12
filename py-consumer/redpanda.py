import asyncio
import logging
import uuid

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

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
        # getmany fetches whatever arrived from all assigned partitions (or {} after
        # the timeout), so the loop can re-check `stop` without task cancellation.
        while not stop.is_set():
            try:
                batches = await consumer.getmany(timeout_ms=500)
            except KafkaError as e:
                # Like Go's fetch-error handling: count it and keep consuming.
                logging.warning("redpanda: fetch error: %s", e)
                errors.inc()
                await asyncio.sleep(1)  # avoid a hot loop on a persistent error
                continue
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
