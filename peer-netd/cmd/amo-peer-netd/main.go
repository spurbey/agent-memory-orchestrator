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
	flag.StringVar(&cfg.StorePath, "store-path", cfg.StorePath, "optional JSONL inbox path for delivered envelopes")
	flag.StringVar(&cfg.IdentityKeyPath, "identity-key", cfg.IdentityKeyPath, "path to a persistent libp2p private key file")
	flag.StringVar(&cfg.SharedSecret, "shared-secret", os.Getenv("AMO_PEER_NETD_SECRET"), "optional shared HMAC secret for AMO envelopes")
	flag.BoolVar(&cfg.RequireSignature, "require-signature", false, "reject unsigned incoming envelopes")
	flag.Var((*stringList)(&cfg.BootstrapAddrs), "bootstrap", "bootstrap peer multiaddr; can be repeated")
	flag.BoolVar(&cfg.EnableMDNS, "mdns", cfg.EnableMDNS, "enable LAN mDNS peer discovery")
	flag.StringVar(&cfg.MDNSServiceTag, "mdns-service", cfg.MDNSServiceTag, "mDNS service tag")
	flag.BoolVar(&cfg.AutoConnectDiscovered, "auto-connect-discovered", cfg.AutoConnectDiscovered, "dial discovered peers automatically")
	flag.BoolVar(&cfg.EnableRendezvous, "rendezvous-server", cfg.EnableRendezvous, "serve AMO rendezvous registration/discovery streams")
	flag.BoolVar(&cfg.EnableRelayService, "relay-service", cfg.EnableRelayService, "serve libp2p circuit relay v2 when reachable")
	flag.BoolVar(&cfg.EnableNATService, "nat-service", cfg.EnableNATService, "help peers determine reachability")
	flag.BoolVar(&cfg.EnableAutoRelay, "auto-relay", cfg.EnableAutoRelay, "enable AutoRelay; requires --static-relay")
	flag.BoolVar(&cfg.EnableHolePunching, "hole-punching", cfg.EnableHolePunching, "enable libp2p DCUtR hole punching")
	flag.BoolVar(&cfg.ForcePrivate, "force-private", cfg.ForcePrivate, "force private reachability for AutoRelay tests")
	flag.BoolVar(&cfg.ForcePublic, "force-public", cfg.ForcePublic, "force public reachability for relay-service tests")
	flag.BoolVar(&cfg.AdvertiseLocalhostDNS, "advertise-localhost-dns", cfg.AdvertiseLocalhostDNS, "advertise 127.0.0.1 listener as dns4/localhost for local relay smoke tests")
	flag.Var((*stringList)(&cfg.AdvertiseAddrs), "advertise-addr", "public libp2p listen multiaddr to advertise; repeat for multiple addresses")
	flag.Var((*stringList)(&cfg.StaticRelayAddrs), "static-relay", "static relay multiaddr; can be repeated")
	flag.Parse()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	st := store.New(cfg.StorePath)
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
	bootstrapResults := node.Bootstrap(ctx, nil)
	relayReservationResults := node.ReserveRelays(ctx, nil)

	info := map[string]any{
		"ok":                        true,
		"node_id":                   cfg.NodeID,
		"peer_id":                   node.PeerID(),
		"listen_addrs":              node.Addrs(),
		"relay_addrs":               node.RelayAddrs(),
		"api_addr":                  api.Addr(),
		"store_path":                st.Path(),
		"store_error":               st.LastError(),
		"bootstrap_results":         bootstrapResults,
		"relay_reservation_results": relayReservationResults,
		"features": map[string]bool{
			"mdns":              cfg.EnableMDNS,
			"rendezvous_server": cfg.EnableRendezvous,
			"relay_service":     cfg.EnableRelayService,
			"auto_relay":        cfg.EnableAutoRelay || len(cfg.StaticRelayAddrs) > 0,
			"hole_punching":     cfg.EnableHolePunching,
		},
	}
	encoded, _ := json.Marshal(info)
	fmt.Println(string(encoded))

	<-ctx.Done()
}

func fatal(err error) {
	fmt.Fprintf(os.Stderr, "amo-peer-netd: %v\n", err)
	os.Exit(1)
}

type stringList []string

func (s *stringList) String() string {
	encoded, _ := json.Marshal([]string(*s))
	return string(encoded)
}

func (s *stringList) Set(value string) error {
	if value == "" {
		return nil
	}
	*s = append(*s, value)
	return nil
}
