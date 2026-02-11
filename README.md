## Summary
Test repo for rabbitmq with flink, includes kafka

### setting up
```sh
docker compose -f kk-rmq-flink-compose.yaml down
docker compose -f kk-rmq-flink-compose.yaml up --build

# one line
docker exec rmq-kafka-flink-benchmark-jobmanager-1 flink run -py /opt/flink/jobs/rmq-job.py
docker exec rmq-kafka-flink-benchmark-jobmanager-1 flink run -py /opt/flink/jobs/kafka-job.py

# check logs
docker logs rmq-kafka-flink-benchmark-taskmanager-1 -f
```

flink dashboard
- http://localhost:8081

kafka topics dashboard
- http://localhost:8082

rabbitmq management
- http://localhost:15762
