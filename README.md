## Summary
Test repo for rabbitmq with flink, includes kafka

### setting up
```sh
docker compose -f kk-rmq-flink-compose.yaml down
docker compose -f kk-rmq-flink-compose.yaml up --build
( set -a; source .env; set +a;  docker compose --env-file .env -f kk-rmq-flink-compose.yaml up --build \
  --scale kafkaproducer=${PRODUCER_SCALE} \
  --scale rabbitmqproducer=${PRODUCER_SCALE} \
  --scale taskmanager=${TASKMANAGER_SCALE} -d )

# one line
docker exec rmq-kafka-flink-benchmark-jobmanager-1 flink run -py /opt/flink/jobs/rmq-job.py
docker exec rmq-kafka-flink-benchmark-jobmanager-1 flink run -py /opt/flink/jobs/kafka-job.py

# check logs
docker logs rmq-kafka-flink-benchmark-taskmanager-1 -f

# check kafka partitions
docker exec -it rmq-kafka-flink-benchmark-kafka-1 \
  kafka-topics.sh --describe \
  --topic events \
  --bootstrap-server kafka:9092

# check rmq streams
docker exec -it rmq-kafka-flink-benchmark-rabbitmq-1 \
  rabbitmqctl start_app

docker exec -it rmq-kafka-flink-benchmark-rabbitmq-1 \
  rabbitmq-streams stream_status events

```

flink dashboard
- http://localhost:8081

kafka topics dashboard
- http://localhost:8082

rabbitmq management
- http://localhost:15762
