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
        # rstream calls this from its own per-partition tasks. No await in here,
        # so two messages never interleave inside it.
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
