package com.example;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.functions.source.RichParallelSourceFunction;

import com.rabbitmq.stream.Environment;
import com.rabbitmq.stream.Consumer;
import com.rabbitmq.stream.OffsetSpecification;

public class RabbitMQSuperStreamJob {

    public static void main(String[] args) throws Exception {
        String host = System.getProperty("rmq.host", "rabbitmq");
        int port = Integer.parseInt(System.getProperty("rmq.port", "5552"));
        String username = System.getProperty("rmq.username", "guest");
        String password = System.getProperty("rmq.password", "guest");
        String baseStreamName = System.getProperty("rmq.stream.name", "events");
        int numPartitions = Integer.parseInt(System.getProperty("rmq.stream.partitions", "8"));

        StreamExecutionEnvironment env =
                StreamExecutionEnvironment.getExecutionEnvironment();

        env.enableCheckpointing(5000);
        env.setParallelism(numPartitions);

        DataStream<String> stream = env.addSource(new SuperStreamSource(
                host, port, username, password, baseStreamName, numPartitions));

        stream
            .map(value -> "Processed from SuperStream: " + value)
            .print();

        env.execute("RabbitMQ SuperStream Job");
    }

    public static class SuperStreamSource extends RichParallelSourceFunction<String> {
        private volatile boolean running = true;
        private final String host;
        private final int port;
        private final String username;
        private final String password;
        private final String baseStreamName;
        private final int numPartitions;
        private Environment environment;
        private Consumer consumer;

        public SuperStreamSource(String host, int port, String username, String password,
                                String baseStreamName, int numPartitions) {
            this.host = host;
            this.port = port;
            this.username = username;
            this.password = password;
            this.baseStreamName = baseStreamName;
            this.numPartitions = numPartitions;
        }

        @Override
        public void run(SourceContext<String> ctx) throws Exception {
            int subtaskIndex = getRuntimeContext().getIndexOfThisSubtask();
            try {
                // 1. Create RabbitMQ environment connection
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 1: Connecting to RabbitMQ at " + host + ":" + port);
                environment = Environment.builder()
                        .host(host)
                        .port(port)
                        .username(username)
                        .password(password)
                        .build();
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 1: SUCCESS - Environment created");

                // 2. Build consumer for superstream
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 2: Building consumer for superstream '" + baseStreamName + "'");
                consumer = environment.consumerBuilder()
                        .superStream(baseStreamName)
                        .offset(OffsetSpecification.first())
                        .messageHandler((context, message) -> {
                            // 4. This callback fires when messages arrive
                            String body = new String(message.getBodyAsBinary());
                            System.err.println("[SUBTASK " + subtaskIndex + "] Step 4: MESSAGE RECEIVED (" + body.length() + " bytes)");
                            synchronized (ctx.getCheckpointLock()) {
                                ctx.collect(body);
                            }
                        })
                        .build();
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 2: SUCCESS - Consumer built, waiting for messages");

                // 3. Keep source alive
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 3: Entering wait loop (will sleep until cancelled)");
                while (running) {
                    Thread.sleep(1000);
                }
                System.err.println("[SUBTASK " + subtaskIndex + "] Step 3: Wait loop exited (source cancelled)");
            } catch (Exception e) {
                System.err.println("[SUBTASK " + subtaskIndex + "] EXCEPTION: " + e.getClass().getSimpleName() + " - " + e.getMessage());
                e.printStackTrace(System.err);
                throw e;
            }
        }

        @Override
        public void cancel() {
            System.err.println("[SOURCE] Cancelling source, cleaning up resources");
            running = false;
            if (consumer != null) {
                try {
                    System.err.println("[SOURCE] Closing consumer");
                    consumer.close();
                    System.err.println("[SOURCE] Consumer closed");
                } catch (Exception e) {
                    System.err.println("[SOURCE] Error closing consumer: " + e.getMessage());
                }
            }
            if (environment != null) {
                try {
                    System.err.println("[SOURCE] Closing RabbitMQ environment");
                    environment.close();
                    System.err.println("[SOURCE] Environment closed");
                } catch (Exception e) {
                    System.err.println("[SOURCE] Error closing environment: " + e.getMessage());
                }
            }
        }
    }
}

