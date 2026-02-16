import os
import json
import time
import random
import asyncio
import uamqp

from rstream import SuperStreamProducer, RouteType, AMQPMessage, ConfirmationStatus 

TOTAL_MESSAGES = int(os.getenv("TOTAL_MESSAGES", "1000000"))
DURATION_SECONDS = int(os.getenv("DURATION_SECONDS", "12"))
STREAM = "events"

TARGET_RATE = TOTAL_MESSAGES / DURATION_SECONDS
INTERVAL = 1.0 / TARGET_RATE

USERS = ["alice", "bob", "carol"]


async def routing_extractor(message: AMQPMessage) -> str:
    # Hash on message_id
    return str(message.properties.message_id)


async def create_producer():
    while True:
        try:
            print("Attempting to connect to RabbitMQ SuperStream...", flush=True)
            producer = SuperStreamProducer(
                host="rabbitmq",
                username="guest",
                password="guest",
                super_stream=STREAM,
                routing=RouteType.Hash,
                routing_extractor=routing_extractor,
            )
            await producer.start()
            print("✅ Connected!", flush=True)
            return producer
        except Exception as e:
            print(f"Connection failed: {e}. Retrying...", flush=True)
            await asyncio.sleep(5)


def _on_publish_confirm_client(confirmation: ConfirmationStatus) -> None:

     if confirmation.is_confirmed == True:
        print("message id: " + str(confirmation.message_id) + " is confirmed")
     else:
         print("message id: " + str(confirmation.message_id) + " is not confirmed")

async def main():
    producer = await create_producer()

    start = time.time()
    next_send = start

    for i in range(TOTAL_MESSAGES):
        message_body = {
            "user": random.choice(USERS),
            "value": random.randint(1, 100),
            "ts": time.time()
        }

        payload = json.dumps(message_body).encode()

        # Proper AMQPMessage with message_id
        # amqp_message = AMQPMessage(
        #     body=payload,
        #     message_id=i,
        #     # properties=uamqp.message.MessageProperties(message_id=i),
        # )
        amqp_message = AMQPMessage(
            body='a:{}'.format(i),
            properties=uamqp.message.MessageProperties(message_id=i),
           
        )

        while True:
            try:
                await producer.send(
                    message=amqp_message,
                    on_publish_confirm=_on_publish_confirm_client,
                )
                break
            except Exception as e:
                print(f"Send failed: {e}. Retrying...", flush=True)
                await asyncio.sleep(2)

        next_send += INTERVAL
        sleep_time = next_send - time.time()
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    await producer.close()

    end = time.time()
    duration = end - start
    print("Completed in:", duration)
    print("Throughput:", int(TOTAL_MESSAGES / duration), "msg/sec")


async def app():
    await main()


if __name__ == "__main__":
    asyncio.run(app())

