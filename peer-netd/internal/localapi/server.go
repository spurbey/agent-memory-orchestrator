package localapi

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"time"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/p2p"
	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
	"github.com/agent-memory-orchestrator/peer-netd/internal/store"
)

type Server struct {
	cfg        config.Config
	node       *p2p.Node
	store      *store.Store
	httpServer *http.Server
	listener   net.Listener
}

func New(cfg config.Config, node *p2p.Node, st *store.Store) *Server {
	return &Server{cfg: cfg, node: node, store: st}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /peers", s.handlePeers)
	mux.HandleFunc("POST /bootstrap", s.handleBootstrap)
	mux.HandleFunc("POST /connect", s.handleConnect)
	mux.HandleFunc("POST /send", s.handleSend)
	mux.HandleFunc("GET /messages", s.handleMessages)
	mux.HandleFunc("POST /rendezvous/register", s.handleRendezvousRegister)
	mux.HandleFunc("POST /rendezvous/discover", s.handleRendezvousDiscover)
	listener, err := net.Listen("tcp", s.cfg.APIAddr)
	if err != nil {
		return err
	}
	s.listener = listener
	s.httpServer = &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = s.httpServer.Serve(listener) }()
	return nil
}

func (s *Server) Addr() string {
	if s.listener == nil {
		return ""
	}
	return s.listener.Addr().String()
}

func (s *Server) Close(ctx context.Context) error {
	if s.httpServer == nil {
		return nil
	}
	return s.httpServer.Shutdown(ctx)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":               true,
		"node_id":          s.cfg.NodeID,
		"peer_id":          s.node.PeerID(),
		"listen_addrs":     s.node.Addrs(),
		"api_addr":         s.Addr(),
		"connected_peers":  s.node.ConnectedPeers(),
		"discovered_peers": s.node.DiscoveredPeers(),
		"message_count":    s.store.Count(),
	})
}

func (s *Server) handlePeers(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":               true,
		"connected_peers":  s.node.ConnectedPeers(),
		"discovered_peers": s.node.DiscoveredPeers(),
	})
}

func (s *Server) handleBootstrap(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Addrs []string `json:"addrs"`
	}
	if err := readJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	results := s.node.Bootstrap(r.Context(), req.Addrs)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "results": results})
}

func (s *Server) handleConnect(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Addr string `json:"addr"`
	}
	if err := readJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := s.node.Connect(r.Context(), req.Addr); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleSend(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ToPeerID string           `json:"to_peer_id"`
		Message  protocol.Message `json:"message"`
	}
	if err := readJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	env, err := s.node.Send(r.Context(), req.ToPeerID, req.Message)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "envelope": env})
}

func (s *Server) handleMessages(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "messages": s.store.List()})
}

func (s *Server) handleRendezvousRegister(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Addr       string `json:"addr"`
		Namespace  string `json:"namespace"`
		TTLSeconds int    `json:"ttl_seconds"`
	}
	if err := readJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := s.node.RegisterWithRendezvous(r.Context(), req.Addr, req.Namespace, time.Duration(req.TTLSeconds)*time.Second); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleRendezvousDiscover(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Addr      string `json:"addr"`
		Namespace string `json:"namespace"`
		Limit     int    `json:"limit"`
		Connect   bool   `json:"connect"`
	}
	if err := readJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	peers, err := s.node.DiscoverViaRendezvous(r.Context(), req.Addr, req.Namespace, req.Limit, req.Connect)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "peers": peers})
}

func readJSON(r *http.Request, dst any) error {
	defer r.Body.Close()
	return json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(dst)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	body, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"ok":false,"error":"json encode failed"}`))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
