package main

import (
	"context"
	"log"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"
)

type redpandaProducer struct {
	client     *kgo.Client
	topic      string
	rateLimit  int // messages per second; 0 = unlimited
	msgSize    int
	producerID string
}

func newRedpandaProducer() *redpandaProducer {
	brokerAddrs := strings.Split(getEnv("BROKER_ADDR", "redpanda:9092"), ",")
	topic := getEnv("TOPIC", "benchmark")
	partitions, _ := strconv.Atoi(getEnv("PARTITIONS", "8"))
	rateLimit, _ := strconv.Atoi(getEnv("RATE_LIMIT", "0"))
	msgSize, _ := strconv.Atoi(getEnv("MSG_SIZE_BYTES", "256"))
	producerID := getEnv("PRODUCER_ID", uuid.New().String()[:8])

	cl, err := kgo.NewClient(
		kgo.SeedBrokers(brokerAddrs...),
		kgo.DefaultProduceTopic(topic),
		// Key-based partitioning ensures a message goes to a consistent partition.
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
	)
	if err != nil {
		log.Fatalf("redpanda: failed to create client: %v", err)
	}

	// Ensure topic exists with the right partition count.
	adm := kadm.NewClient(cl)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	_, err = adm.CreateTopics(ctx, int32(partitions), 1, nil, topic)
	if err != nil {
		// Topic may already exist — not fatal.
		log.Printf("redpanda: create topic: %v (may already exist)", err)
	}

	log.Printf("redpanda producer started: id=%s topic=%s partitions=%d rateLimit=%d msgSize=%d",
		producerID, topic, partitions, rateLimit, msgSize)

	return &redpandaProducer{
		client:     cl,
		topic:      topic,
		rateLimit:  rateLimit,
		msgSize:    msgSize,
		producerID: producerID,
	}
}

func (p *redpandaProducer) run(ctx context.Context) error {
	defer p.client.Close()

	var throttle <-chan time.Time
	if p.rateLimit > 0 {
		ticker := time.NewTicker(time.Second / time.Duration(p.rateLimit))
		defer ticker.Stop()
		throttle = ticker.C
	}

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		if throttle != nil {
			select {
			case <-ctx.Done():
				return nil
			case <-throttle:
			}
		}

		id := uuid.New().String()
		msg := newMessage(id, p.msgSize)
		data, err := encodeMessage(msg)
		if err != nil {
			log.Printf("redpanda: encode error: %v", err)
			continue
		}

		sendTime := time.Now()
		record := &kgo.Record{
			Key:   []byte(id),
			Value: data,
		}

		// Produce is async; the callback fires when the broker acknowledges.
		p.client.Produce(ctx, record, func(_ *kgo.Record, err error) {
			if err != nil {
				produceErrors.WithLabelValues("redpanda", p.producerID).Inc()
				return
			}
			elapsed := time.Since(sendTime).Seconds()
			msgLen := float64(len(data))
			messagesProduced.WithLabelValues("redpanda", p.producerID).Inc()
			bytesProduced.WithLabelValues("redpanda", p.producerID).Add(msgLen)
			produceAckLatency.WithLabelValues("redpanda", p.producerID).Observe(elapsed)
		})
	}
}
