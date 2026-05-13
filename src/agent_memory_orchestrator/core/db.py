from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  owner_user_id TEXT NOT NULL DEFAULT 'local',
  workspace_id TEXT NOT NULL DEFAULT 'local',
  project_id TEXT NOT NULL DEFAULT 'default',
  visibility_scope TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  event_type TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  source_app TEXT NOT NULL DEFAULT 'unknown',
  owner_user_id TEXT NOT NULL DEFAULT 'local',
  workspace_id TEXT NOT NULL DEFAULT 'local',
  project_id TEXT NOT NULL DEFAULT 'default',
  visibility_scope TEXT NOT NULL DEFAULT 'private',
  sensitivity_level TEXT NOT NULL DEFAULT 'normal',
  redacted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
  UNIQUE(event_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS memory_units (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  source_chunk_id TEXT,
  memory_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  summary TEXT NOT NULL,
  topic_key TEXT NOT NULL DEFAULT '',
  entities_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.4,
  importance REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'active',
  owner_user_id TEXT NOT NULL DEFAULT 'local',
  workspace_id TEXT NOT NULL DEFAULT 'local',
  project_id TEXT NOT NULL DEFAULT 'default',
  visibility_scope TEXT NOT NULL DEFAULT 'private',
  sensitivity_level TEXT NOT NULL DEFAULT 'normal',
  supersedes_memory_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (source_event_id) REFERENCES events(id) ON DELETE CASCADE,
  FOREIGN KEY (source_chunk_id) REFERENCES chunks(id) ON DELETE SET NULL,
  FOREIGN KEY (supersedes_memory_id) REFERENCES memory_units(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  importance REAL NOT NULL DEFAULT 0.5,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (source_event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_vectors (
  memory_id TEXT PRIMARY KEY,
  dims INTEGER NOT NULL,
  model TEXT NOT NULL DEFAULT 'hash-fallback',
  backend TEXT NOT NULL DEFAULT 'sqlite',
  vector_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (memory_id) REFERENCES memory_units(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_embeddings (
  embedding_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  node_kind TEXT NOT NULL,
  memory_class TEXT NOT NULL,
  graph_scope TEXT NOT NULL,
  graph_path TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  extraction_run_id TEXT NOT NULL DEFAULT '',
  embedding_kind TEXT NOT NULL,
  model TEXT NOT NULL,
  dims INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  vector_json TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  memory_tier TEXT NOT NULL DEFAULT 'hot',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_accessed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(entity_type, normalized_name)
);

CREATE TABLE IF NOT EXISTS kg_edges (
  id TEXT PRIMARY KEY,
  source_entity_id TEXT NOT NULL,
  target_entity_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  evidence_memory_id TEXT,
  evidence_chunk_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  confidence REAL NOT NULL DEFAULT 0.5,
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
  FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
  FOREIGN KEY (evidence_memory_id) REFERENCES memory_units(id) ON DELETE SET NULL,
  FOREIGN KEY (evidence_chunk_id) REFERENCES chunks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS session_summaries (
  session_id TEXT PRIMARY KEY,
  summary_text TEXT NOT NULL,
  key_decisions_json TEXT NOT NULL DEFAULT '[]',
  open_blockers_json TEXT NOT NULL DEFAULT '[]',
  changed_files_json TEXT NOT NULL DEFAULT '[]',
  evidence_memory_ids_json TEXT NOT NULL DEFAULT '[]',
  generator TEXT NOT NULL DEFAULT 'rule_v1',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS index_versions (
  id TEXT PRIMARY KEY,
  index_type TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  backend TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  session_id TEXT,
  source_event_id TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS extraction_runs (
  id TEXT PRIMARY KEY,
  pipeline_run_id TEXT,
  session_id TEXT NOT NULL,
  source_chunk_id TEXT,
  extractor TEXT NOT NULL,
  memory_count INTEGER NOT NULL DEFAULT 0,
  confidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE SET NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (source_chunk_id) REFERENCES chunks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
  id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  intent TEXT NOT NULL,
  session_id TEXT,
  include_historical INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS retrieval_candidates (
  id TEXT PRIMARY KEY,
  retrieval_run_id TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  source TEXT NOT NULL,
  rank INTEGER NOT NULL,
  raw_score REAL NOT NULL DEFAULT 0.0,
  rrf_score REAL NOT NULL DEFAULT 0.0,
  rerank_score REAL NOT NULL DEFAULT 0.0,
  final_score REAL NOT NULL DEFAULT 0.0,
  score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (retrieval_run_id) REFERENCES retrieval_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (memory_id) REFERENCES memory_units(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS consolidation_decisions (
  id TEXT PRIMARY KEY,
  new_memory_id TEXT NOT NULL,
  related_memory_id TEXT,
  relation TEXT NOT NULL,
  score REAL NOT NULL,
  score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  decision_status TEXT NOT NULL DEFAULT 'applied',
  created_at TEXT NOT NULL,
  FOREIGN KEY (new_memory_id) REFERENCES memory_units(id) ON DELETE CASCADE,
  FOREIGN KEY (related_memory_id) REFERENCES memory_units(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS approval_decisions (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  decision_type TEXT NOT NULL,
  decision TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orchestration_rounds (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  round_index INTEGER NOT NULL,
  agent TEXT NOT NULL,
  summary TEXT NOT NULL,
  artifact_uri TEXT NOT NULL DEFAULT '',
  blocking_issues_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orchestration_decisions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  decided_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated
ON sessions(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_session_created
ON events(session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_created
ON events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chunks_session_event
ON chunks(session_id, event_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_memory_units_session_created
ON memory_units(session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_units_created
ON memory_units(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_units_topic_status
ON memory_units(topic_key, status);

CREATE INDEX IF NOT EXISTS idx_memory_units_project_visibility
ON memory_units(project_id, visibility_scope, status);

CREATE INDEX IF NOT EXISTS idx_graph_embeddings_node
ON graph_embeddings(node_id, embedding_kind, model, status);

CREATE INDEX IF NOT EXISTS idx_graph_embeddings_lookup
ON graph_embeddings(embedding_kind, model, graph_scope, status);

CREATE INDEX IF NOT EXISTS idx_graph_embeddings_session
ON graph_embeddings(session_id, extraction_run_id, status);

CREATE INDEX IF NOT EXISTS idx_entities_normalized
ON entities(normalized_name);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source_relation
ON kg_edges(source_entity_id, relation, status);

CREATE INDEX IF NOT EXISTS idx_retrieval_candidates_run
ON retrieval_candidates(retrieval_run_id, final_score DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_runs_started
ON retrieval_runs(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_rounds_session_round
ON orchestration_rounds(session_id, round_index DESC);
"""


FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_units_fts USING fts5(
  memory_id UNINDEXED,
  summary,
  subject,
  object,
  topic_key
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        # Another process may hold a lock during startup. The busy timeout
        # still protects normal reads/writes; WAL will be enabled on a later
        # connection when the lock clears.
        pass
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _existing_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _run_light_migrations(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "sessions", "owner_user_id", "owner_user_id TEXT NOT NULL DEFAULT 'local'")
    _add_column_if_missing(conn, "sessions", "workspace_id", "workspace_id TEXT NOT NULL DEFAULT 'local'")
    _add_column_if_missing(conn, "sessions", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _add_column_if_missing(conn, "sessions", "visibility_scope", "visibility_scope TEXT NOT NULL DEFAULT 'private'")

    _add_column_if_missing(conn, "events", "source_app", "source_app TEXT NOT NULL DEFAULT 'unknown'")
    _add_column_if_missing(conn, "events", "owner_user_id", "owner_user_id TEXT NOT NULL DEFAULT 'local'")
    _add_column_if_missing(conn, "events", "workspace_id", "workspace_id TEXT NOT NULL DEFAULT 'local'")
    _add_column_if_missing(conn, "events", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _add_column_if_missing(conn, "events", "visibility_scope", "visibility_scope TEXT NOT NULL DEFAULT 'private'")
    _add_column_if_missing(conn, "events", "sensitivity_level", "sensitivity_level TEXT NOT NULL DEFAULT 'normal'")
    _add_column_if_missing(conn, "events", "redacted", "redacted INTEGER NOT NULL DEFAULT 0")

    _add_column_if_missing(conn, "memory_vectors", "model", "model TEXT NOT NULL DEFAULT 'hash-fallback'")
    _add_column_if_missing(conn, "memory_vectors", "backend", "backend TEXT NOT NULL DEFAULT 'sqlite'")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _run_light_migrations(conn)
    try:
        conn.executescript(FTS_SQL)
    except sqlite3.OperationalError:
        # Some embedded SQLite builds omit FTS5. Retrieval falls back to LIKE/vector.
        pass
    conn.commit()
