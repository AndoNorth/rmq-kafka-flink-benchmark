package main

import (
	"context"
	"fmt"
	"log"
	"strconv"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/rabbitmq/rabbitmq-stream-go-client/pkg/amqp"
	"github.com/rabbitmq/rabbitmq-stream-go-client/pkg/message"
	"github.com/rabbitmq/rabbitmq-stream-go-client/pkg/stream"
)

type rabbitMQProducer struct {
	env         *stream.Environment
	superStream string
	partitions  int
	rateLimit   int
	msgSize     int
	producerID  string

	mu        sync.Mutex
	sendTimes map[string]time.Time
	msgBytes  map[string]int
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

func newRabbitMQProducer() *rabbitMQProducer {
	host := getEnv("BROKER_HOST", "rabbitmq")
	port, _ := strconv.Atoi(getEnv("BROKER_PORT", "5552"))
	user := getEnv("BROKER_USER", "guest")
	pass := getEnv("BROKER_PASS", "guest")
	superStream := getEnv("TOPIC", "benchmark")
	partitions, _ := strconv.Atoi(getEnv("PARTITIONS", "8"))
	rateLimit, _ := strconv.Atoi(getEnv("RATE_LIMIT", "0"))
	msgSize, _ := strconv.Atoi(getEnv("MSG_SIZE_BYTES", "256"))
	producerID := getEnv("PRODUCER_ID", uuid.New().String()[:8])

	env := connectRabbitMQEnv(host, port, user, pass)

	// Declare super stream (idempotent).
	if err := env.DeclareSuperStream(superStream, stream.NewPartitionsOptions(partitions)); err != nil {
		log.Printf("rabbitmq: declare super stream: %v (may already exist)", err)
	}

	log.Printf("rabbitmq producer: id=%s superStream=%s partitions=%d rateLimit=%d msgSize=%d",
		producerID, superStream, partitions, rateLimit, msgSize)

	return &rabbitMQProducer{
		env:         env,
		superStream: superStream,
		partitions:  partitions,
		rateLimit:   rateLimit,
		msgSize:     msgSize,
		producerID:  producerID,
		sendTimes:   make(map[string]time.Time),
		msgBytes:    make(map[string]int),
	}
}

func (p *rabbitMQProducer) run(ctx context.Context) error {
	defer p.env.Close()

	routingStrategy := stream.NewHashRoutingStrategy(func(msg message.StreamMessage) string {
		props := msg.GetApplicationProperties()
		if key, ok := props["routing-key"].(string); ok {
			return key
		}
		return ""
	})

	producer, err := p.env.NewSuperStreamProducer(
		p.superStream,
		stream.NewSuperStreamProducerOptions(routingStrategy),
	)
	if err != nil {
		return fmt.Errorf("rabbitmq: create super stream producer: %w", err)
	}
	defer producer.Close()

	chConfirm := producer.NotifyPublishConfirmation(10_000)

	go func() {
		for partConfirm := range chConfirm {
			for _, cs := range partConfirm.ConfirmationStatus {
				id := ""
				if props := cs.GetMessage().GetApplicationProperties(); props != nil {
					if v, ok := props["routing-key"].(string); ok {
						id = v
					}
				}

				p.mu.Lock()
				t := p.sendTimes[id]
				sz := p.msgBytes[id]
				delete(p.sendTimes, id)
				delete(p.msgBytes, id)
				p.mu.Unlock()

				if cs.IsConfirmed() {
					if !t.IsZero() {
						produceAckLatency.WithLabelValues("rabbitmq", p.producerID).Observe(time.Since(t).Seconds())
						bytesProduced.WithLabelValues("rabbitmq", p.producerID).Add(float64(sz))
					}
					messagesProduced.WithLabelValues("rabbitmq", p.producerID).Inc()
				} else {
					produceErrors.WithLabelValues("rabbitmq", p.producerID).Inc()
				}
			}
		}
	}()

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
			log.Printf("rabbitmq: encode: %v", err)
			continue
		}

		amqpMsg := amqp.NewMessage(data)
		amqpMsg.ApplicationProperties = map[string]interface{}{"routing-key": id}

		p.mu.Lock()
		p.sendTimes[id] = time.Now()
		p.msgBytes[id] = len(data)
		p.mu.Unlock()

		if err := producer.Send(amqpMsg); err != nil {
			log.Printf("rabbitmq: send: %v", err)
			produceErrors.WithLabelValues("rabbitmq", p.producerID).Inc()
			p.mu.Lock()
			delete(p.sendTimes, id)
			delete(p.msgBytes, id)
			p.mu.Unlock()
		}
	}
}
