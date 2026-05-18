package config

import "time"

type Config struct {
	NodeID           string
	ListenAddr       string
	APIAddr          string
	SharedSecret     string
	RequireSignature bool
	DialTimeout      time.Duration
}

func Default() Config {
	return Config{
		NodeID:      "amo-node",
		ListenAddr:  "/ip4/127.0.0.1/tcp/0",
		APIAddr:     "127.0.0.1:0",
		DialTimeout: 10 * time.Second,
	}
}
