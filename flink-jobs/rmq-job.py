from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors.rabbitmq import RMQSource, RMQConnectionConfig

env = StreamExecutionEnvironment.get_execution_environment()

env.enable_checkpointing(5000)

# Build connection config
connection_config = RMQConnectionConfig.Builder() \
    .set_host("rabbitmq") \
    .set_port(5672) \
    .set_user_name("guest") \
    .set_password("guest") \
    .set_virtual_host("/") \
    .build()

source = RMQSource(
    connection_config,
    "events",
    True,  # use correlation ids
    SimpleStringSchema()
)

stream = env.add_source(source)

stream \
    .map(lambda x: f"Processed by RMQ: {x}") \
    .print()

env.execute("RabbitMQ Stream Job")

