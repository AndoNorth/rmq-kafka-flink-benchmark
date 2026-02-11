import json
import time
import random
from kafka import KafkaProducer

producer = None

# Retry until Kafka is ready
while producer is None:
    try:
        producer = KafkaProducer(
            bootstrap_servers='kafka:9092',
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
    except Exception as e:
        print("Waiting for Kafka...")
        time.sleep(5)

topic = "events"
print("Kafka producer started. Sending events...")

while True:
    message = {
        "user": random.choice(["alice", "bob", "carol"]),
        "value": random.randint(1, 100)
    }
    producer.send(topic, value=message)
    print("Sent:", message)
    time.sleep(1)

