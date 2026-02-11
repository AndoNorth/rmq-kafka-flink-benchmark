import pika
import json
import time
import random
import sys

def connect():
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host="rabbitmq")
            )
            return connection
        except Exception as e:
            print("Waiting for RabbitMQ...")
            time.sleep(5)

connection = connect()
channel = connection.channel()

channel.queue_declare(queue="events", durable=True)

print("Producer started. Sending events...")

while True:
    message = {
        "user": random.choice(["alice", "bob", "carol"]),
        "value": random.randint(1, 100)
    }

    channel.basic_publish(
        exchange="",
        routing_key="events",
        body=json.dumps(message),
        properties=pika.BasicProperties(
            delivery_mode=2,
            correlation_id='1234'
        )
    )

    print("Sent:", message)
    time.sleep(1)

