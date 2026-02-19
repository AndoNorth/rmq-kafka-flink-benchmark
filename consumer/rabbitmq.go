package main

import (
	"context"
	"fmt"
	"log"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/rabbitmq/rabbitmq-stream-go-client/pkg/amqp"
	"github.com/rabbitmq/rabbitmq-stream-go-client/pkg/stream"
)

type rabbitMQConsumer struct {
	env         *stream.Environment
	superStream string
	consumerID  string
}

func connectRabbitMQEnv(host string, port int, user, pass string) *stream.Environment {
	opts := stream.NewEnvironmentOptions().
		SetHost(host).
		SetPort(port).
		SetUser(user).
		SetPassword(pass)
	for {
		env, err := stream.NewEnvironment(opts)
		if err == nil {
			return env
		}
		log.Printf("rabbitmq: connect failed (%v), retrying in 3s…", err)
		time.Sleep(3 * time.Second)
	}
}

func newRabbitMQConsumer() *rabbitMQConsumer {
	host := getEnv("BROKER_HOST", "rabbitmq")
	port, _ := strconv.Atoi(getEnv("BROKER_PORT", "5552"))
	user := getEnv("BROKER_USER", "guest")
	pass := getEnv("BROKER_PASS", "guest")
	superStream := getEnv("TOPIC", "benchmark")
	consumerID := getEnv("CONSUMER_ID", uuid.New().String()[:8])

	env := connectRabbitMQEnv(host, port, user, pass)

	log.Printf("rabbitmq consumer: id=%s superStream=%s", consumerID, superStream)
	return &rabbitMQConsumer{env: env, superStream: superStream, consumerID: consumerID}
}

func (c *rabbitMQConsumer) run(ctx context.Context) error {
	defer c.env.Close()

	handleMessage := func(_ stream.ConsumerContext, msg *amqp.Message) {
		if len(msg.Data) == 0 {
			return
		}
		raw := msg.Data[0]
		decoded, err := decodeMessage(raw)
		if err != nil {
			log.Printf("rabbitmq: decode: %v", err)
			consumeErrors.WithLabelValues("rabbitmq", c.consumerID).Inc()
			return
		}

		messagesConsumed.WithLabelValues("rabbitmq", c.consumerID).Inc()
		bytesConsumed.WithLabelValues("rabbitmq", c.consumerID).Add(float64(len(raw)))
		e2eLatency.WithLabelValues("rabbitmq").Observe(decoded.latencySeconds())
	}

	// Single Active Consumer: all instances sharing ConsumerName form a group;
	// RabbitMQ assigns each partition to exactly one active instance.
	sac := stream.NewSingleActiveConsumer(func(_ string, _ bool) stream.OffsetSpecification {
		return stream.OffsetSpecification{}.Next()
	})

	opts := stream.NewSuperStreamConsumerOptions().
		SetConsumerName("benchmark-consumer").
		SetOffset(stream.OffsetSpecification{}.Next()).
		SetSingleActiveConsumer(sac)

	superConsumer, err := c.env.NewSuperStreamConsumer(c.superStream, handleMessage, opts)
	if err != nil {
		return fmt.Errorf("rabbitmq: create super stream consumer: %w", err)
	}
	defer superConsumer.Close()

	<-ctx.Done()
	return nil
}
