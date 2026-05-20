package p2p

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	libp2p "github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/peerstore"
	"github.com/libp2p/go-libp2p/p2p/discovery/mdns"
	"github.com/libp2p/go-libp2p/p2p/host/autorelay"
	relayclient "github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/client"
	"github.com/multiformats/go-multiaddr"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
	"github.com/agent-memory-orchestrator/peer-netd/internal/rendezvous"
	"github.com/agent-memory-orchestrator/peer-netd/internal/store"
)

type Node struct {
	cfg        config.Config
	host       host.Host
	store      *store.Store
	mdns       mdns.Service
	rendezvous *rendezvous.Registry

	discoveredMu sync.Mutex
	discovered   map[peer.ID]peer.AddrInfo
	relayMu      sync.Mutex
	relayAddrs   map[string]string
}

type BootstrapResult struct {
	Addr  string `json:"addr"`
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

func New(ctx context.Context, cfg config.Config, st *store.Store) (*Node, error) {
	_ = ctx
	listen, err := multiaddr.NewMultiaddr(cfg.ListenAddr)
	if err != nil {
		return nil, fmt.Errorf("invalid listen addr: %w", err)
	}
	if cfg.ForcePrivate && cfg.ForcePublic {
		return nil, errors.New("force-private and force-public are mutually exclusive")
	}

	opts := []libp2p.Option{libp2p.ListenAddrs(listen)}
	if cfg.AdvertiseLocalhostDNS || len(cfg.AdvertiseAddrs) > 0 {
		advertiseAddrs, err := multiaddrsFromStrings(cfg.AdvertiseAddrs)
		if err != nil {
			return nil, fmt.Errorf("invalid advertise addr: %w", err)
		}
		opts = append(opts, libp2p.AddrsFactory(func(addrs []multiaddr.Multiaddr) []multiaddr.Multiaddr {
			return advertiseAddrsFactory(addrs, advertiseAddrs, cfg.AdvertiseLocalhostDNS)
		}))
	}
	if cfg.EnableRelayService {
		opts = append(opts, libp2p.DisableRelay(), libp2p.EnableRelayService())
	}
	if cfg.EnableNATService {
		opts = append(opts, libp2p.EnableNATService())
	}
	if cfg.EnableHolePunching {
		opts = append(opts, libp2p.EnableHolePunching())
	}
	if cfg.ForcePrivate {
		opts = append(opts, libp2p.ForceReachabilityPrivate())
	}
	if cfg.ForcePublic {
		opts = append(opts, libp2p.ForceReachabilityPublic())
	}
	if len(cfg.StaticRelayAddrs) > 0 {
		relays, err := addrInfosFromStrings(cfg.StaticRelayAddrs)
		if err != nil {
			return nil, fmt.Errorf("invalid static relay addr: %w", err)
		}
		opts = append(opts, libp2p.EnableAutoRelayWithStaticRelays(
			relays,
			autorelay.WithBootDelay(0),
			autorelay.WithMinCandidates(1),
			autorelay.WithNumRelays(min(2, len(relays))),
		))
	} else if cfg.EnableAutoRelay {
		return nil, errors.New("auto relay requires at least one --static-relay")
	}

	h, err := libp2p.New(opts...)
	if err != nil {
		return nil, err
	}
	n := &Node{
		cfg:        cfg,
		host:       h,
		store:      st,
		rendezvous: rendezvous.NewRegistry(),
		discovered: make(map[peer.ID]peer.AddrInfo),
		relayAddrs: make(map[string]string),
	}
	h.SetStreamHandler(protocol.ProtocolID, n.handleStream)
	if cfg.EnableRendezvous {
		h.SetStreamHandler(rendezvous.ProtocolID, n.handleRendezvousStream)
	}
	if cfg.EnableMDNS {
		service := mdns.NewMdnsService(h, cfg.MDNSServiceTag, n)
		if err := service.Start(); err != nil {
			_ = h.Close()
			return nil, fmt.Errorf("start mdns discovery: %w", err)
		}
		n.mdns = service
	}
	return n, nil
}

func (n *Node) Close() error {
	if n.mdns != nil {
		_ = n.mdns.Close()
	}
	return n.host.Close()
}

func (n *Node) PeerID() string {
	return n.host.ID().String()
}

func (n *Node) Addrs() []string {
	out := make([]string, 0, len(n.host.Addrs()))
	for _, addr := range n.host.Addrs() {
		out = append(out, addr.String()+"/p2p/"+n.host.ID().String())
	}
	n.relayMu.Lock()
	defer n.relayMu.Unlock()
	for _, addr := range n.relayAddrs {
		if !containsString(out, addr) {
			out = append(out, addr)
		}
	}
	return out
}

func (n *Node) RelayAddrs() []string {
	addrs := n.Addrs()
	out := make([]string, 0, len(addrs))
	for _, addr := range addrs {
		if strings.Contains(addr, "/p2p-circuit") {
			out = append(out, addr)
		}
	}
	n.relayMu.Lock()
	defer n.relayMu.Unlock()
	for _, addr := range n.relayAddrs {
		if !containsString(out, addr) {
			out = append(out, addr)
		}
	}
	return out
}

func (n *Node) ConnectedPeers() []string {
	peers := n.host.Network().Peers()
	out := make([]string, 0, len(peers))
	for _, p := range peers {
		out = append(out, p.String())
	}
	return out
}

func (n *Node) DiscoveredPeers() []rendezvous.PeerInfo {
	n.discoveredMu.Lock()
	defer n.discoveredMu.Unlock()
	out := make([]rendezvous.PeerInfo, 0, len(n.discovered))
	for _, info := range n.discovered {
		out = append(out, rendezvous.PeerInfo{ID: info.ID.String(), Addrs: addrStrings(info.Addrs, info.ID)})
	}
	return out
}

func (n *Node) Bootstrap(ctx context.Context, addrs []string) []BootstrapResult {
	if len(addrs) == 0 {
		addrs = n.cfg.BootstrapAddrs
	}
	results := make([]BootstrapResult, 0, len(addrs))
	for _, addr := range addrs {
		err := n.Connect(ctx, addr)
		result := BootstrapResult{Addr: addr, OK: err == nil}
		if err != nil {
			result.Error = err.Error()
		}
		results = append(results, result)
	}
	return results
}

func (n *Node) ReserveRelays(ctx context.Context, addrs []string) []BootstrapResult {
	if len(addrs) == 0 {
		addrs = n.cfg.StaticRelayAddrs
	}
	results := make([]BootstrapResult, 0, len(addrs))
	for _, addr := range addrs {
		relayAddr, err := n.ReserveRelay(ctx, addr)
		result := BootstrapResult{Addr: addr, OK: err == nil}
		if err != nil {
			result.Error = err.Error()
		} else {
			result.Addr = relayAddr
		}
		results = append(results, result)
	}
	return results
}

func (n *Node) ReserveRelay(ctx context.Context, addr string) (string, error) {
	info, err := addrInfoFromString(addr)
	if err != nil {
		return "", err
	}
	n.addPeerInfo(*info)
	dialCtx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	if err := n.host.Connect(dialCtx, *info); err != nil {
		return "", err
	}
	reserveCtx, reserveCancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer reserveCancel()
	if _, err := relayclient.Reserve(reserveCtx, n.host, *info); err != nil {
		return "", err
	}
	relayAddr := addr + "/p2p-circuit/p2p/" + n.host.ID().String()
	n.relayMu.Lock()
	n.relayAddrs[info.ID.String()] = relayAddr
	n.relayMu.Unlock()
	return relayAddr, nil
}

func (n *Node) Connect(ctx context.Context, addr string) error {
	info, err := addrInfoFromString(addr)
	if err != nil {
		return err
	}
	n.addPeerInfo(*info)
	dialCtx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	return n.host.Connect(dialCtx, *info)
}

func (n *Node) Send(ctx context.Context, toPeerID string, msg protocol.Message) (protocol.Envelope, error) {
	pid, err := peer.Decode(toPeerID)
	if err != nil {
		return protocol.Envelope{}, err
	}
	if msg.FromNode == "" {
		msg.FromNode = n.cfg.NodeID
	}
	env, err := protocol.NewEnvelope(msg, n.cfg.SharedSecret)
	if err != nil {
		return protocol.Envelope{}, err
	}
	dialCtx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	dialCtx = network.WithUseTransient(dialCtx, "amo peer message may use relay fallback")
	stream, err := n.host.NewStream(dialCtx, pid, protocol.ProtocolID)
	if err != nil {
		return protocol.Envelope{}, err
	}
	defer stream.Close()
	enc := json.NewEncoder(stream)
	if err := enc.Encode(env); err != nil {
		return protocol.Envelope{}, err
	}
	return env, nil
}

func (n *Node) HandlePeerFound(info peer.AddrInfo) {
	if info.ID == n.host.ID() {
		return
	}
	n.addPeerInfo(info)
	if !n.cfg.AutoConnectDiscovered {
		return
	}
	go func() {
		dialCtx, cancel := context.WithTimeout(context.Background(), n.cfg.DialTimeout)
		defer cancel()
		_ = n.host.Connect(dialCtx, info)
	}()
}

func (n *Node) RegisterWithRendezvous(ctx context.Context, addr string, namespace string, ttl time.Duration) error {
	resp, err := n.rendezvousRequest(ctx, addr, rendezvous.Request{
		Type:       "register",
		Namespace:  namespace,
		Peer:       n.selfPeerInfo(),
		TTLSeconds: int(ttl.Seconds()),
	})
	if err != nil {
		return err
	}
	if !resp.OK {
		return errors.New(resp.Error)
	}
	return nil
}

func (n *Node) DiscoverViaRendezvous(ctx context.Context, addr string, namespace string, limit int, connect bool) ([]rendezvous.PeerInfo, error) {
	resp, err := n.rendezvousRequest(ctx, addr, rendezvous.Request{
		Type:      "discover",
		Namespace: namespace,
		Limit:     limit,
		ExcludeID: n.host.ID().String(),
	})
	if err != nil {
		return nil, err
	}
	if !resp.OK {
		return nil, errors.New(resp.Error)
	}
	for _, item := range resp.Peers {
		info, err := peerInfoFromRendezvous(item)
		if err != nil {
			continue
		}
		n.addPeerInfo(*info)
		if connect {
			dialCtx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
			_ = n.host.Connect(dialCtx, *info)
			cancel()
		}
	}
	return resp.Peers, nil
}

func (n *Node) handleStream(stream network.Stream) {
	defer stream.Close()
	reader := bufio.NewReader(io.LimitReader(stream, 1<<20))
	dec := json.NewDecoder(reader)
	for {
		var env protocol.Envelope
		if err := dec.Decode(&env); err != nil {
			return
		}
		if err := protocol.VerifyEnvelope(env, n.cfg.SharedSecret, n.cfg.RequireSignature); err != nil {
			n.store.Add(protocol.Envelope{
				Version:    protocol.EnvelopeVersion,
				FromNodeID: "__invalid__",
				CreatedAt:  time.Now().UTC().Format(time.RFC3339Nano),
				Message:    protocol.Message{Type: "invalid_envelope", FromNode: "__invalid__", Payload: map[string]any{"error": err.Error()}},
			})
			return
		}
		n.store.Add(env)
	}
}

func (n *Node) handleRendezvousStream(stream network.Stream) {
	defer stream.Close()
	dec := json.NewDecoder(io.LimitReader(stream, 1<<20))
	enc := json.NewEncoder(stream)
	var req rendezvous.Request
	if err := dec.Decode(&req); err != nil {
		_ = enc.Encode(rendezvous.Response{OK: false, Error: err.Error()})
		return
	}
	switch req.Type {
	case "register":
		ttl := time.Duration(req.TTLSeconds) * time.Second
		if err := n.rendezvous.Register(req.Namespace, req.Peer, ttl); err != nil {
			_ = enc.Encode(rendezvous.Response{OK: false, Error: err.Error()})
			return
		}
		_ = enc.Encode(rendezvous.Response{OK: true})
	case "discover":
		peers := n.rendezvous.Discover(req.Namespace, req.ExcludeID, req.Limit)
		_ = enc.Encode(rendezvous.Response{OK: true, Peers: peers})
	default:
		_ = enc.Encode(rendezvous.Response{OK: false, Error: "unknown rendezvous request type"})
	}
}

func (n *Node) rendezvousRequest(ctx context.Context, addr string, req rendezvous.Request) (rendezvous.Response, error) {
	info, err := addrInfoFromString(addr)
	if err != nil {
		return rendezvous.Response{}, err
	}
	n.addPeerInfo(*info)
	dialCtx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	if err := n.host.Connect(dialCtx, *info); err != nil {
		return rendezvous.Response{}, err
	}
	stream, err := n.host.NewStream(dialCtx, info.ID, rendezvous.ProtocolID)
	if err != nil {
		return rendezvous.Response{}, err
	}
	defer stream.Close()
	if err := json.NewEncoder(stream).Encode(req); err != nil {
		return rendezvous.Response{}, err
	}
	var resp rendezvous.Response
	if err := json.NewDecoder(io.LimitReader(stream, 1<<20)).Decode(&resp); err != nil {
		return rendezvous.Response{}, err
	}
	return resp, nil
}

func (n *Node) addPeerInfo(info peer.AddrInfo) {
	if info.ID == "" {
		return
	}
	n.host.Peerstore().AddAddrs(info.ID, info.Addrs, peerstore.PermanentAddrTTL)
	n.discoveredMu.Lock()
	defer n.discoveredMu.Unlock()
	n.discovered[info.ID] = info
}

func (n *Node) selfPeerInfo() rendezvous.PeerInfo {
	return rendezvous.PeerInfo{ID: n.host.ID().String(), Addrs: n.Addrs()}
}

func addrInfoFromString(addr string) (*peer.AddrInfo, error) {
	ma, err := multiaddr.NewMultiaddr(addr)
	if err != nil {
		return nil, err
	}
	return peer.AddrInfoFromP2pAddr(ma)
}

func addrInfosFromStrings(addrs []string) ([]peer.AddrInfo, error) {
	out := make([]peer.AddrInfo, 0, len(addrs))
	for _, addr := range addrs {
		info, err := addrInfoFromString(addr)
		if err != nil {
			return nil, err
		}
		out = append(out, *info)
	}
	return out, nil
}

func multiaddrsFromStrings(addrs []string) ([]multiaddr.Multiaddr, error) {
	out := make([]multiaddr.Multiaddr, 0, len(addrs))
	for _, addr := range addrs {
		ma, err := multiaddr.NewMultiaddr(addr)
		if err != nil {
			return nil, err
		}
		out = append(out, ma)
	}
	return out, nil
}

func peerInfoFromRendezvous(item rendezvous.PeerInfo) (*peer.AddrInfo, error) {
	id, err := peer.Decode(item.ID)
	if err != nil {
		return nil, err
	}
	addrs := make([]multiaddr.Multiaddr, 0, len(item.Addrs))
	for _, addr := range item.Addrs {
		ma, err := multiaddr.NewMultiaddr(addr)
		if err != nil {
			return nil, err
		}
		info, err := peer.AddrInfoFromP2pAddr(ma)
		if err != nil {
			return nil, err
		}
		addrs = append(addrs, info.Addrs...)
	}
	return &peer.AddrInfo{ID: id, Addrs: addrs}, nil
}

func addrStrings(addrs []multiaddr.Multiaddr, id peer.ID) []string {
	out := make([]string, 0, len(addrs))
	for _, addr := range addrs {
		out = append(out, addr.String()+"/p2p/"+id.String())
	}
	return out
}

func containsString(items []string, target string) bool {
	for _, item := range items {
		if item == target {
			return true
		}
	}
	return false
}

func min(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func advertiseAddrsFactory(addrs []multiaddr.Multiaddr, advertised []multiaddr.Multiaddr, localhostDNS bool) []multiaddr.Multiaddr {
	out := make([]multiaddr.Multiaddr, len(addrs))
	copy(out, addrs)
	if localhostDNS {
		for i, addr := range out {
			text := addr.String()
			if strings.HasPrefix(text, "/ip4/127.0.0.1/") {
				out[i] = multiaddr.StringCast("/dns4/localhost" + strings.TrimPrefix(text, "/ip4/127.0.0.1"))
			}
		}
	}
	for _, addr := range advertised {
		if !containsMultiaddr(out, addr) {
			out = append(out, addr)
		}
	}
	return out
}

func containsMultiaddr(items []multiaddr.Multiaddr, target multiaddr.Multiaddr) bool {
	for _, item := range items {
		if item.Equal(target) {
			return true
		}
	}
	return false
}
