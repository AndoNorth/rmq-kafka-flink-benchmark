import json
import time
import random
import os
from kafka import KafkaProducer

# ===============================
# Configuration via ENV variables
# ===============================
BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.getenv("TOPIC", "events")
TOTAL_MESSAGES = int(os.getenv("TOTAL_MESSAGES", "1000000"))
DURATION_SECONDS = int(os.getenv("DURATION_SECONDS", "12"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32768"))
LINGER_MS = int(os.getenv("LINGER_MS", "5"))
ACKS = os.getenv("ACKS", "0")  # 0, 1, or all
KEYED = os.getenv("KEYED", "true").lower() == "true"

# Calculate target rate
TARGET_RATE = TOTAL_MESSAGES / DURATION_SECONDS

print(f"""
Kafka Load Test Config
----------------------
Messages: {TOTAL_MESSAGES}
Duration: {DURATION_SECONDS}s
Target rate: {int(TARGET_RATE)} msg/sec
""")

# Connect
producer = None
while producer is None:
    try:
        producer = KafkaProducer(
            bootstrap_servers=BOOTSTRAP,
            batch_size=BATCH_SIZE,
            linger_ms=LINGER_MS,
            acks=ACKS,
            buffer_memory=134217728,   # 128MB
            max_block_ms=120000,
            value_serializer=lambda v: json.dumps(v).encode(),
            key_serializer=lambda v: v.encode() if v else None
        )

    except Exception:
        print("Waiting for Kafka...")
        time.sleep(3)

start = time.time()
interval = 1.0 / TARGET_RATE
next_send = time.time()

for i in range(TOTAL_MESSAGES):
    message = {
        "user": random.choice(["alice", "bob", "carol"]),
        "value": random.randint(1, 100),
        "ts": time.time()
    }

    key = message["user"] if KEYED else None

    producer.send(TOPIC, value=message, key=key)

    next_send += interval
    sleep_time = next_send - time.time()
    if sleep_time > 0:
        time.sleep(sleep_time)


producer.flush()
end = time.time()

actual_rate = TOTAL_MESSAGES / (end - start)

print(f"Completed in {end - start:.2f}s")
print(f"Actual throughput: {int(actual_rate)} msg/sec")

