package main

import (
	"context"
	"log"
	"strings"

	"github.com/google/uuid"
	"github.com/twmb/franz-go/pkg/kgo"
)

type redpandaConsumer struct {
	client     *kgo.Client
	consumerID string
}

func newRedpandaConsumer() *redpandaConsumer {
	brokerAddrs := strings.Split(getEnv("BROKER_ADDR", "redpanda:9092"), ",")
	topic := getEnv("TOPIC", "benchmark")
	// All consumer instances sharing the same group will each consume a
	// distinct subset of partitions, giving true horizontal scale-out.
	consumerGroup := getEnv("CONSUMER_GROUP", "benchmark-consumers")
	consumerID := getEnv("CONSUMER_ID", uuid.New().String()[:8])

	cl, err := kgo.NewClient(
		kgo.SeedBrokers(brokerAddrs...),
		kgo.ConsumerGroup(consumerGroup),
		kgo.ConsumeTopics(topic),
		// Start from the latest offset so we only measure messages produced
		// during the benchmark run.
		kgo.ConsumeResetOffset(kgo.NewOffset().AtEnd()),
	)
	if err != nil {
		log.Fatalf("redpanda: failed to create consumer: %v", err)
	}

	log.Printf("redpanda consumer started: id=%s group=%s topic=%s", consumerID, consumerGroup, topic)
	return &redpandaConsumer{client: cl, consumerID: consumerID}
}

func (c *redpandaConsumer) run(ctx context.Context) error {
	defer c.client.Close()

	for {
		fetches := c.client.PollFetches(ctx)
		if fetches.IsClientClosed() {
			return nil
		}
		if ctx.Err() != nil {
			return nil
		}

		fetches.EachError(func(t string, p int32, err error) {
			log.Printf("redpanda: fetch error topic=%s partition=%d: %v", t, p, err)
			consumeErrors.WithLabelValues("redpanda", c.consumerID).Inc()
		})

		fetches.EachRecord(func(r *kgo.Record) {
			msg, err := decodeMessage(r.Value)
			if err != nil {
				log.Printf("redpanda: decode error: %v", err)
				consumeErrors.WithLabelValues("redpanda", c.consumerID).Inc()
				return
			}

			latency := msg.latencySeconds()
			messagesConsumed.WithLabelValues("redpanda", c.consumerID).Inc()
			bytesConsumed.WithLabelValues("redpanda", c.consumerID).Add(float64(len(r.Value)))
			e2eLatency.WithLabelValues("redpanda").Observe(latency)
		})
	}
}
