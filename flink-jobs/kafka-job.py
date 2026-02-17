import os 
import json
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer
# from pyflink.datastream.connectors.kafka import KafkaDeserializationSchema
# from org.apache.kafka.clients.consumer import ConsumerRecord

env = StreamExecutionEnvironment.get_execution_environment()
env.enable_checkpointing(5000)
# Set parallelism equal to Kafka partitions
env.set_parallelism(8)

PID=os.getpid()

# class MyKafkaSchema(KafkaDeserializationSchema):
#
#     def is_end_of_stream(self, record):
#         return False
#
#     def deserialize(self, record: ConsumerRecord):
#         # record.value() is the message value
#         # record.partition() is partition
#         # record.offset() is offset
#         return {
#             "partition": record.partition(),
#             "offset": record.offset(),
#             "value": record.value().decode("utf-8")
#         }
#
#     def get_produced_type(self):
#         from pyflink.common import Types
#         return Types.PICKLED_BYTE_ARRAY()  # or pick a dict type

# Kafka consumer config
kafka_props = {
    'bootstrap.servers': 'kafka:9092',
    'group.id': f'kk-flink-consumer-{PID}',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': 'false'
}

source = FlinkKafkaConsumer(
    topics='events',
    # deserialization_schema=MyKafkaSchema(),
    deserialization_schema=SimpleStringSchema(),
    properties=kafka_props
)


stream = env.add_source(source)

# stream.map(lambda x: f"Partition: {x['partition']}, Offset: {x['offset']}, Value: {x['value']}").print()
stream.map(lambda x: f"Value from Kafka: {x}").print().set_parallelism(1)

env.execute("Kafka Stream Job")

