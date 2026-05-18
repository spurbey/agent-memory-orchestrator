package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/localapi"
	"github.com/agent-memory-orchestrator/peer-netd/internal/p2p"
	"github.com/agent-memory-orchestrator/peer-netd/internal/store"
)

func main() {
	cfg := config.Default()
	flag.StringVar(&cfg.NodeID, "node-id", cfg.NodeID, "stable AMO node id")
	flag.StringVar(&cfg.ListenAddr, "listen", cfg.ListenAddr, "libp2p listen multiaddr")
	flag.StringVar(&cfg.APIAddr, "api", cfg.APIAddr, "local HTTP API bind address")
	flag.StringVar(&cfg.SharedSecret, "shared-secret", os.Getenv("AMO_PEER_NETD_SECRET"), "optional shared HMAC secret for AMO envelopes")
	flag.BoolVar(&cfg.RequireSignature, "require-signature", false, "reject unsigned incoming envelopes")
	flag.Parse()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	st := store.New()
	node, err := p2p.New(ctx, cfg, st)
	if err != nil {
		fatal(err)
	}
	defer node.Close()

	api := localapi.New(cfg, node, st)
	if err := api.Start(); err != nil {
		fatal(err)
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = api.Close(shutdownCtx)
	}()

	info := map[string]any{
		"ok":           true,
		"node_id":      cfg.NodeID,
		"peer_id":      node.PeerID(),
		"listen_addrs": node.Addrs(),
		"api_addr":     api.Addr(),
	}
	encoded, _ := json.Marshal(info)
	fmt.Println(string(encoded))

	<-ctx.Done()
}

func fatal(err error) {
	fmt.Fprintf(os.Stderr, "amo-peer-netd: %v\n", err)
	os.Exit(1)
}
