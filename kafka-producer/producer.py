import json
import time
import random
import os

from kafka import KafkaProducer
from kafka.errors import KafkaError

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.getenv("TOPIC", "events")

TOTAL_MESSAGES = int(os.getenv("TOTAL_MESSAGES", "1000000"))
DURATION_SECONDS = int(os.getenv("DURATION_SECONDS", "12"))

# for pure throughput:
# acks=1,linger_ms=20,batchsize=131072,compression_type='lz4'
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32768"))
LINGER_MS = int(os.getenv("LINGER_MS", "5"))
ACKS = os.getenv("ACKS", "all")   # use all for RMQ-like safety

TARGET_RATE = TOTAL_MESSAGES / DURATION_SECONDS
INTERVAL = 1.0 / TARGET_RATE

USERS = ["alice", "bob", "carol"]
PID = os.getpid()

print(f"""
Kafka Load Test Config
----------------------
Messages: {TOTAL_MESSAGES}
Duration: {DURATION_SECONDS}s
Target rate: {int(TARGET_RATE)} msg/sec
""")

def on_send_success(record_metadata):
    print(f"message delivered to {record_metadata.topic} "
          f"partition {record_metadata.partition} "
          f"offset {record_metadata.offset}")

def on_send_error(excp):
    print(f"message failed: {excp}")

# Connect loop like your RMQ producer
producer = None
while producer is None:
    try:
        print("Attempting to connect to Kafka...", flush=True)

        producer = KafkaProducer(
            bootstrap_servers=BOOTSTRAP,
            batch_size=BATCH_SIZE,
            linger_ms=LINGER_MS,
            acks=ACKS,
            retries=10,
            buffer_memory=134217728,
            max_block_ms=120000,
            value_serializer=lambda v: json.dumps(v).encode(),
            key_serializer=lambda v: v.encode() if v else None
        )

        print("✅ Connected!", flush=True)

    except Exception as e:
        print(f"Connection failed: {e}. Retrying...", flush=True)
        time.sleep(5)

start = time.time()
next_send = start

for i in range(TOTAL_MESSAGES):

    message_body = {
        "user": random.choice(USERS),
        "value": random.randint(1, 100),
        "ts": time.time(),
        "message_id": f"{PID}-{i}"
    }

    # Equivalent of routing_extractor hash on "id"
    key = str(i)   # This matches RMQ hash routing

    while True:
        try:
            future = producer.send(
                TOPIC,
                key=key,
                value=message_body
            )
            future.add_callback(on_send_success)
            future.add_errback(on_send_error)
            break
        except KafkaError as e:
            print(f"Send failed: {e}. Retrying...", flush=True)
            time.sleep(2)

    # Rate control (same as RMQ version)
    next_send += INTERVAL
    sleep_time = next_send - time.time()
    if sleep_time > 0:
        time.sleep(sleep_time)

producer.flush()

end = time.time()
duration = end - start

print("Completed in:", duration, " secs")
print("Throughput:", float(TOTAL_MESSAGES / duration), "msg/sec")

