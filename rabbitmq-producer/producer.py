import os
import json
import time
import random
import asyncio

from rstream import SuperStreamProducer, RouteType, AMQPMessage, ConfirmationStatus, Properties

TOTAL_MESSAGES = int(os.getenv("TOTAL_MESSAGES", "1000000"))
DURATION_SECONDS = int(os.getenv("DURATION_SECONDS", "12"))
STREAM = "events"

MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "50"))

TARGET_RATE = TOTAL_MESSAGES / DURATION_SECONDS
INTERVAL = 1.0 / TARGET_RATE

USERS = ["alice", "bob", "carol"]
PID = os.getpid()

print(f"""
RabbitMQ Concurrent Load Test Config
-------------------------------------
Messages: {TOTAL_MESSAGES}
Duration: {DURATION_SECONDS}s
Target rate: {int(TARGET_RATE)} msg/sec
Max in-flight: {MAX_IN_FLIGHT}
""")

async def routing_extractor(message: AMQPMessage) -> str:
    return message.application_properties["id"]

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
    if not confirmation.is_confirmed:
        print(f"❌ message {confirmation.message_id} not confirmed")

async def send_with_retry(producer, message, semaphore):
    async with semaphore:
        while True:
            try:
                await producer.send(
                    message=message,
                    on_publish_confirm=_on_publish_confirm_client,
                )
                return
            except Exception as e:
                print(f"Send failed: {e}. Retrying...", flush=True)
                await asyncio.sleep(2)

async def main():
    producer = await create_producer()
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)

    start = time.time()
    next_send = start
    tasks = []

    for i in range(TOTAL_MESSAGES):

        message_body = {
            "user": random.choice(USERS),
            "value": random.randint(1, 100),
            "ts": time.time()
        }

        payload = json.dumps(message_body).encode()

        amqp_message = AMQPMessage(
            body=payload,
            properties=Properties(message_id=f"{PID}-{i}"),
            application_properties={
                "id": str(i),
                "pid": str(PID),
            }
        )

        task = asyncio.create_task(
            send_with_retry(producer, amqp_message, semaphore)
        )
        tasks.append(task)

        # Rate limiting
        next_send += INTERVAL
        sleep_time = next_send - time.time()
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

        # Prevent tasks list from growing forever
        if len(tasks) >= MAX_IN_FLIGHT:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            tasks = list(pending)

    # Wait for remaining sends
    await asyncio.gather(*tasks)

    await producer.close()

    end = time.time()
    duration = end - start
    print("Completed in:", duration, " secs")
    print("Throughput:", float(TOTAL_MESSAGES / duration), "msg/sec")

if __name__ == "__main__":
    asyncio.run(main())

