export type AntelligentStatus = {
  ok: boolean;
  daemon?: {
    ok: boolean;
    host: string;
    port: number;
    local_only: boolean;
  };
  peer?: {
    ok?: boolean;
    node?: {
      node_id: string;
      display_name: string;
      capabilities: string[];
      share_boundary: string;
    };
    peers?: PeerNode[];
    room_count?: number;
    error?: string;
  };
  netd?: {
    ok?: boolean;
    running?: boolean;
    api_ok?: boolean;
    pid?: number | null;
    api_url?: string | null;
    error?: string;
  };
  worker?: {
    ok: boolean;
    enabled: boolean;
    normal_worker: string;
    netd_api_ok: boolean;
    room_count: number;
  };
  llm?: {
    ok: boolean;
    local_ollama_ready: boolean;
    provider_configured: boolean;
    retrieval_only_fallback: boolean;
  };
};

export type PeerNode = {
  node_id: string;
  display_name?: string;
  capabilities?: string[];
  trust?: string;
  peer_id?: string;
};

export type AgentRoom = {
  room_id: string;
  topic?: string;
  initiator_node_id?: string;
  participants?: string[];
  updated_at?: string;
  agent_state?: AgentState;
};

export type AgentState = {
  status?: string;
  original_query?: string;
  finalized_reason?: string;
  final?: {
    answer?: string;
    mode?: string;
    confidence?: number;
    citations?: SupportRef[];
  };
};

export type RoomMessage = {
  message_id: string;
  type: string;
  from_node_id?: string;
  from?: string;
  to_node_ids?: string[];
  content?: string;
  confidence?: number;
  citations?: string[];
  metadata?: Record<string, unknown>;
  created_at?: string;
};

export type RoomContext = {
  room_id: string;
  role: string;
  layers?: {
    room_md?: string;
    rolling_summary_md?: string;
    room_roster?: Array<Record<string, unknown>>;
    group_recent_messages?: RoomMessage[];
    pairwise_recent_messages?: RoomMessage[];
    recent_messages?: RoomMessage[];
    open_questions?: Array<Record<string, unknown>>;
  };
  policy_projection?: Record<string, unknown>;
  context_text?: string;
};

export type SupportRef = {
  source_peer?: string;
  visibility?: string;
  local_ref?: Record<string, string>;
  shared_ref?: Record<string, string>;
  claim?: string;
  claim_sha256?: string;
};

export type ChatResult = {
  ok: boolean;
  mode: string;
  answer: string;
  room_id: string;
  peer_responses?: Array<Record<string, unknown>>;
  citations?: SupportRef[];
  reason?: string;
  timing?: Record<string, unknown>;
  error?: string;
};

export type AntelligentEvent = {
  type: string;
  payload: Record<string, unknown>;
  created_at_ms?: number;
};
