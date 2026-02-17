package com.example;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.functions.source.SourceFunction;

import com.rabbitmq.stream.Environment;
import com.rabbitmq.stream.Consumer;
import com.rabbitmq.stream.OffsetSpecification;

public class RabbitMQSuperStreamJob {

    public static void main(String[] args) throws Exception {

        StreamExecutionEnvironment env =
                StreamExecutionEnvironment.getExecutionEnvironment();

        env.enableCheckpointing(5000);
        env.setParallelism(8); // match your partition count

        DataStream<String> stream = env.addSource(new SuperStreamSource("events"));

        stream
            .map(value -> "Processed from SuperStream: " + value)
            .print();

        env.execute("RabbitMQ SuperStream Job");
    }

    public static class SuperStreamSource implements SourceFunction<String> {

        private volatile boolean running = true;
        private final String superStream;

        public SuperStreamSource(String superStream) {
            this.superStream = superStream;
        }

        @Override
        public void run(SourceContext<String> ctx) throws Exception {

            Environment environment = Environment.builder()
                    .host("rabbitmq")
                    .port(5552)
                    .username("guest")
                    .password("guest")
                    .build();

            Consumer consumer = environment.consumerBuilder()
                    .superStream(superStream)
                    .offset(OffsetSpecification.first())
                    .messageHandler((context, message) -> {
                        String body = new String(message.getBodyAsBinary());
                        synchronized (ctx.getCheckpointLock()) {
                            ctx.collect(body);
                        }
                    })
                    .build();

            while (running) {
                Thread.sleep(1000);
            }

            consumer.close();
            environment.close();
        }

        @Override
        public void cancel() {
            running = false;
        }
    }
}

