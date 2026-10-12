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
