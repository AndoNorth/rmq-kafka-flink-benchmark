package main

import (
	"log"
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	messagesConsumed = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_messages_consumed_total",
		Help: "Total number of messages consumed.",
	}, []string{"broker", "consumer_id"})

	bytesConsumed = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_bytes_consumed_total",
		Help: "Total bytes of message payloads consumed.",
	}, []string{"broker", "consumer_id"})

	consumeErrors = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "benchmark_consume_errors_total",
		Help: "Total number of consume or decode errors.",
	}, []string{"broker", "consumer_id"})

	// e2eLatency captures the full publish-to-consume path including broker
	// transit time.  Buckets are biased toward sub-second values.
	e2eLatency = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "benchmark_e2e_latency_seconds",
		Help: "End-to-end latency from producer publish to consumer receive.",
		Buckets: []float64{
			.0001, .0005, .001, .002, .005,
			.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 7.5, 10,
			25, 50, 100, 250, 500, 1000,
			2500, 5000, 10000,
			25000, 50000, 100000,
		},
	}, []string{"broker"})
)

func serveMetrics(addr string) {
	http.Handle("/metrics", promhttp.Handler())
	log.Printf("metrics listening on %s", addr)
	if err := http.ListenAndServe(addr, nil); err != nil {
		log.Fatalf("metrics server error: %v", err)
	}
}
