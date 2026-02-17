from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors.rabbitmq import RMQSource, RMQConnectionConfig

NUM_PARTITIONS = 8

env = StreamExecutionEnvironment.get_execution_environment()
env.set_parallelism(NUM_PARTITIONS)
env.enable_checkpointing(5000)

connection_config = RMQConnectionConfig.Builder() \
    .set_host("rabbitmq") \
    .set_port(5672) \
    .set_user_name("guest") \
    .set_password("guest") \
    .set_virtual_host("/") \
    .set_prefetch_count(500) \
    .build()

streams = []

for i in range(NUM_PARTITIONS):
    queue_name = f"events-{i}"

    source = RMQSource(
        connection_config,
        queue_name,
        True,
        SimpleStringSchema()
    )

    streams.append(env.add_source(source).name(queue_name))

# Union all partition streams
stream = streams[0]
for s in streams[1:]:
    stream = stream.union(s)

stream \
    .map(lambda x: f"Processed by RMQ: {x}") \
    .print().set_parallelism(1)

env.execute("RabbitMQ SuperStream via AMQP Job")

