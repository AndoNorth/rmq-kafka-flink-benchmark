package main

import (
	"encoding/json"
	"time"
)

// Message is the benchmark event payload. PublishTimeNs is set by the producer
// so consumers can compute end-to-end latency without clock synchronisation
// issues (both producer and consumer run inside the same Docker network).
type Message struct {
	ID            string `json:"id"`
	PublishTimeNs int64  `json:"publish_time_ns"` // Unix nanoseconds at publish time
	Payload       string `json:"payload"`
}

func newMessage(id string, payloadSize int) *Message {
	return &Message{
		ID:            id,
		PublishTimeNs: time.Now().UnixNano(),
		Payload:       string(make([]byte, payloadSize)),
	}
}

func encodeMessage(m *Message) ([]byte, error) {
	return json.Marshal(m)
}
