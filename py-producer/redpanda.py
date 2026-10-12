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
