from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer

env = StreamExecutionEnvironment.get_execution_environment()
env.enable_checkpointing(5000)

# Kafka consumer config
kafka_props = {
    'bootstrap.servers': 'kafka:9092',
    'group.id': 'flink-consumer-group',
    'auto.offset.reset': 'earliest'
}

source = FlinkKafkaConsumer(
    topics='events',
    deserialization_schema=SimpleStringSchema(),
    properties=kafka_props
)

stream = env.add_source(source)

stream \
    .map(lambda x: f"Processed from Kafka: {x}") \
    .print()

env.execute("Kafka Stream Job")

