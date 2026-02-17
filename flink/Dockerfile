FROM flink:1.18

USER root

RUN apt-get update && \
    apt-get install -y git maven python3 python3-pip wget && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    apt-get clean

RUN pip3 install apache-flink==1.18.0 rstream==1.0.0

# Add Flink RabbitMQ connector
RUN mkdir -p /opt/flink/lib && \
    wget -O /opt/flink/lib/flink-connector-rabbitmq_2.12-1.14.6.jar \
    https://repo1.maven.org/maven2/org/apache/flink/flink-connector-rabbitmq_2.12/1.14.6/flink-connector-rabbitmq_2.12-1.14.6.jar && \
    wget -O /opt/flink/lib/amqp-client-5.16.0.jar \
    https://repo1.maven.org/maven2/com/rabbitmq/amqp-client/5.16.0/amqp-client-5.16.0.jar

# Add Flink Kafka connector
RUN wget -O /opt/flink/lib/flink-connector-kafka_2.12-1.14.6.jar \
    https://repo1.maven.org/maven2/org/apache/flink/flink-connector-kafka_2.12/1.14.6/flink-connector-kafka_2.12-1.14.6.jar && \
    wget -O /opt/flink/lib/kafka-clients-3.5.1.jar \
    https://repo1.maven.org/maven2/org/apache/kafka/kafka-clients/3.5.1/kafka-clients-3.5.1.jar



USER flink

