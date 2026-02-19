package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
)

func getEnv(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

func main() {
	brokerType := getEnv("BROKER_TYPE", "redpanda")
	metricsAddr := getEnv("METRICS_ADDR", ":2112")

	go serveMetrics(metricsAddr)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		<-sigCh
		log.Println("shutting down producer")
		cancel()
	}()

	var err error
	switch brokerType {
	case "redpanda", "kafka":
		p := newRedpandaProducer()
		err = p.run(ctx)
	case "rabbitmq":
		p := newRabbitMQProducer()
		err = p.run(ctx)
	default:
		log.Fatalf("unknown BROKER_TYPE=%q, expected redpanda or rabbitmq", brokerType)
	}

	if err != nil {
		log.Printf("producer exited: %v", err)
	}
}
