package main

import (
	"encoding/json"
	"time"
)

// Message mirrors the producer's payload. PublishTimeNs is used to compute
// end-to-end latency: latency = now - PublishTimeNs.
type Message struct {
	ID            string `json:"id"`
	PublishTimeNs int64  `json:"publish_time_ns"`
	Payload       string `json:"payload"`
}

func decodeMessage(data []byte) (*Message, error) {
	var m Message
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

func (m *Message) latencySeconds() float64 {
	return float64(time.Now().UnixNano()-m.PublishTimeNs) / 1e9
}
