package protocol

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/google/uuid"
)

const (
	ProtocolID      = "/amo/peer/1.0.0"
	EnvelopeVersion = 1
)

type Message struct {
	Type      string         `json:"type"`
	RoomID    string         `json:"room_id,omitempty"`
	FromNode  string         `json:"from_node_id"`
	ToNode    string         `json:"to_node_id,omitempty"`
	Payload   map[string]any `json:"payload,omitempty"`
	Citations []string       `json:"citations,omitempty"`
	Metadata  map[string]any `json:"metadata,omitempty"`
	CreatedAt string         `json:"created_at,omitempty"`
}

type Envelope struct {
	Version       int     `json:"amo_peer_envelope_version"`
	FromNodeID    string  `json:"from_node_id"`
	RemotePeerID  string  `json:"remote_peer_id,omitempty"`
	CreatedAt     string  `json:"created_at"`
	Nonce         string  `json:"nonce"`
	PayloadSHA256 string  `json:"payload_sha256"`
	Message       Message `json:"message"`
	Signature     string  `json:"signature,omitempty"`
}

func NewEnvelope(msg Message, secret string) (Envelope, error) {
	if msg.Type == "" {
		return Envelope{}, errors.New("message type is required")
	}
	if msg.FromNode == "" {
		return Envelope{}, errors.New("from_node_id is required")
	}
	if msg.CreatedAt == "" {
		msg.CreatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	env := Envelope{
		Version:    EnvelopeVersion,
		FromNodeID: msg.FromNode,
		CreatedAt:  time.Now().UTC().Format(time.RFC3339Nano),
		Nonce:      uuid.NewString(),
		Message:    msg,
	}
	hash, err := canonicalSHA256(msg)
	if err != nil {
		return Envelope{}, err
	}
	env.PayloadSHA256 = hash
	if secret != "" {
		sig, err := signEnvelope(env, secret)
		if err != nil {
			return Envelope{}, err
		}
		env.Signature = "hmac-sha256:" + sig
	}
	return env, nil
}

func VerifyEnvelope(env Envelope, secret string, requireSignature bool) error {
	if env.Version != EnvelopeVersion {
		return fmt.Errorf("unsupported envelope version: %d", env.Version)
	}
	if env.FromNodeID == "" {
		return errors.New("from_node_id is required")
	}
	if env.Message.FromNode != "" && env.Message.FromNode != env.FromNodeID {
		return fmt.Errorf("envelope sender mismatch: %s != %s", env.FromNodeID, env.Message.FromNode)
	}
	hash, err := canonicalSHA256(env.Message)
	if err != nil {
		return err
	}
	if !hmac.Equal([]byte(hash), []byte(env.PayloadSHA256)) {
		return errors.New("payload hash mismatch")
	}
	if requireSignature || env.Signature != "" {
		if secret == "" {
			return errors.New("shared secret is required to verify signature")
		}
		expected, err := signEnvelope(env, secret)
		if err != nil {
			return err
		}
		actual := env.Signature
		if len(actual) > len("hmac-sha256:") && actual[:len("hmac-sha256:")] == "hmac-sha256:" {
			actual = actual[len("hmac-sha256:"):]
		}
		if !hmac.Equal([]byte(expected), []byte(actual)) {
			return errors.New("signature mismatch")
		}
	}
	return nil
}

func signEnvelope(env Envelope, secret string) (string, error) {
	payload := map[string]any{
		"amo_peer_envelope_version": env.Version,
		"from_node_id":              env.FromNodeID,
		"created_at":                env.CreatedAt,
		"nonce":                     env.Nonce,
		"payload_sha256":            env.PayloadSHA256,
	}
	data, err := canonicalJSON(payload)
	if err != nil {
		return "", err
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(data)
	return hex.EncodeToString(mac.Sum(nil)), nil
}

func canonicalSHA256(v any) (string, error) {
	data, err := canonicalJSON(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func canonicalJSON(v any) ([]byte, error) {
	normalized := normalize(v)
	return json.Marshal(normalized)
}

func normalize(v any) any {
	switch t := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := make(map[string]any, len(t))
		for _, k := range keys {
			out[k] = normalize(t[k])
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, item := range t {
			out[i] = normalize(item)
		}
		return out
	case []string:
		out := make([]any, len(t))
		for i, item := range t {
			out[i] = item
		}
		return out
	case Message:
		return map[string]any{
			"type":         t.Type,
			"room_id":      t.RoomID,
			"from_node_id": t.FromNode,
			"to_node_id":   t.ToNode,
			"payload":      normalize(t.Payload),
			"citations":    normalize(t.Citations),
			"metadata":     normalize(t.Metadata),
			"created_at":   t.CreatedAt,
		}
	default:
		return t
	}
}
