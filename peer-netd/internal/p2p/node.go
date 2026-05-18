package p2p

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"time"

	libp2p "github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/peerstore"
	"github.com/multiformats/go-multiaddr"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
	"github.com/agent-memory-orchestrator/peer-netd/internal/store"
)

type Node struct {
	cfg   config.Config
	host  host.Host
	store *store.Store
}

func New(ctx context.Context, cfg config.Config, st *store.Store) (*Node, error) {
	listen, err := multiaddr.NewMultiaddr(cfg.ListenAddr)
	if err != nil {
		return nil, fmt.Errorf("invalid listen addr: %w", err)
	}
	h, err := libp2p.New(libp2p.ListenAddrs(listen))
	if err != nil {
		return nil, err
	}
	n := &Node{cfg: cfg, host: h, store: st}
	h.SetStreamHandler(protocol.ProtocolID, n.handleStream)
	return n, nil
}

func (n *Node) Close() error {
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

func (n *Node) Connect(ctx context.Context, addr string) error {
	ma, err := multiaddr.NewMultiaddr(addr)
	if err != nil {
		return err
	}
	info, err := peer.AddrInfoFromP2pAddr(ma)
	if err != nil {
		return err
	}
	n.host.Peerstore().AddAddrs(info.ID, info.Addrs, peerstore.PermanentAddrTTL)
	ctx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	return n.host.Connect(ctx, *info)
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
	ctx, cancel := context.WithTimeout(ctx, n.cfg.DialTimeout)
	defer cancel()
	stream, err := n.host.NewStream(ctx, pid, protocol.ProtocolID)
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
