package main

import (
	"log"
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	messagesProduced = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_messages_produced_total",
		Help: "Total number of messages successfully produced.",
	}, []string{"broker", "producer_id"})

	bytesProduced = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_bytes_produced_total",
		Help: "Total bytes of message payloads produced.",
	}, []string{"broker", "producer_id"})

	produceErrors = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_produce_errors_total",
		Help: "Total number of produce errors.",
	}, []string{"broker", "producer_id"})

	produceAckLatency = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "benchmark_produce_ack_latency_seconds",
		Help:    "Time between send and broker acknowledgement.",
		Buckets: []float64{.0001, .0005, .001, .005, .01, .025, .05, .1, .25, .5, 1, 2.5},
	}, []string{"broker", "producer_id"})
)

func serveMetrics(addr string) {
	http.Handle("/metrics", promhttp.Handler())
	log.Printf("metrics listening on %s", addr)
	if err := http.ListenAndServe(addr, nil); err != nil {
		log.Fatalf("metrics server error: %v", err)
	}
}
