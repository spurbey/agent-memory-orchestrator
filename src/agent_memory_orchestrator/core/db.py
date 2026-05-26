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

CREATE TABLE IF NOT EXISTS v2_session_jobs (
  job_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL DEFAULT 'v2-reset-2026-05',
  graph_schema_version TEXT NOT NULL DEFAULT 'v2',
  status TEXT NOT NULL DEFAULT 'pending',
  current_stage TEXT NOT NULL DEFAULT '',
  last_successful_stage TEXT NOT NULL DEFAULT '',
  artifact_dir TEXT NOT NULL DEFAULT '',
  source_app TEXT NOT NULL DEFAULT '',
  repo_path TEXT NOT NULL DEFAULT '',
  repo_id TEXT NOT NULL DEFAULT '',
  boundary_event_id TEXT NOT NULL DEFAULT '',
  source_evidence_day TEXT NOT NULL DEFAULT '',
  source_evidence_days_json TEXT NOT NULL DEFAULT '[]',
  lock_owner TEXT NOT NULL DEFAULT '',
  lock_expires_at TEXT NOT NULL DEFAULT '',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT NOT NULL DEFAULT '',
  forced_at TEXT NOT NULL DEFAULT '',
  forced_by TEXT NOT NULL DEFAULT '',
  error_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(session_id, pipeline_version)
);

CREATE TABLE IF NOT EXISTS v2_session_job_stages (
  job_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input_artifact TEXT NOT NULL DEFAULT '',
  output_artifact TEXT NOT NULL DEFAULT '',
  input_hash TEXT NOT NULL DEFAULT '',
  output_hash TEXT NOT NULL DEFAULT '',
  stage_config_hash TEXT NOT NULL DEFAULT '',
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(job_id, stage),
  FOREIGN KEY (job_id) REFERENCES v2_session_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_session_job_events (
  event_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES v2_session_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_production_markers (
  marker_key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_central_merge_plans (
  plan_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL DEFAULT 'v2-reset-2026-05',
  graph_schema_version TEXT NOT NULL DEFAULT 'v2',
  plan_version TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  mode TEXT NOT NULL DEFAULT 'dry_run',
  repo_id TEXT NOT NULL DEFAULT '',
  repo_path TEXT NOT NULL DEFAULT '',
  parent_graph_commit_id TEXT NOT NULL DEFAULT '',
  input_graph_hash TEXT NOT NULL DEFAULT '',
  plan_hash TEXT NOT NULL DEFAULT '',
  plan_json TEXT NOT NULL DEFAULT '{}',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES v2_session_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_central_review_candidates (
  candidate_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  source_node_id TEXT NOT NULL DEFAULT '',
  target_node_id TEXT NOT NULL DEFAULT '',
  proposed_relation TEXT NOT NULL DEFAULT '',
  score_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (plan_id) REFERENCES v2_central_merge_plans(plan_id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES v2_session_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_central_decision_frames (
  frame_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  repo_id TEXT NOT NULL DEFAULT '',
  source_node_id TEXT NOT NULL DEFAULT '',
  frame_kind TEXT NOT NULL DEFAULT '',
  source_scope TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  statement TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'review',
  frame_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (plan_id) REFERENCES v2_central_merge_plans(plan_id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES v2_session_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_graph_commits (
  graph_commit_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL DEFAULT '',
  job_id TEXT NOT NULL DEFAULT '',
  repo_id TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL DEFAULT 'main',
  parent_graph_commit_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  pipeline_version TEXT NOT NULL DEFAULT 'v2-reset-2026-05',
  graph_schema_version TEXT NOT NULL DEFAULT 'v2',
  algorithm_versions_json TEXT NOT NULL DEFAULT '{}',
  added_nodes_json TEXT NOT NULL DEFAULT '[]',
  added_edges_json TEXT NOT NULL DEFAULT '[]',
  status_updates_json TEXT NOT NULL DEFAULT '[]',
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_graph_views (
  view_id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL DEFAULT 'main',
  mode TEXT NOT NULL DEFAULT 'active',
  graph_commit_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(repo_id, branch, mode, status)
);

CREATE TABLE IF NOT EXISTS v2_central_merge_locks (
  repo_id TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL DEFAULT 'main',
  lock_owner TEXT NOT NULL DEFAULT '',
  lock_expires_at TEXT NOT NULL DEFAULT '',
  expected_parent_graph_commit_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(repo_id, branch)
);

CREATE TABLE IF NOT EXISTS v2_semantic_eval_runs (
  run_id TEXT PRIMARY KEY,
  case_set TEXT NOT NULL DEFAULT '',
  fixture_path TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_semantic_eval_cases (
  case_id TEXT PRIMARY KEY,
  case_set TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL DEFAULT '',
  case_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_semantic_eval_judgments (
  judgment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  case_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  scores_json TEXT NOT NULL DEFAULT '{}',
  explanation TEXT NOT NULL DEFAULT '',
  blocking_failures_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES v2_semantic_eval_runs(run_id) ON DELETE CASCADE
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

CREATE INDEX IF NOT EXISTS idx_v2_session_jobs_status
ON v2_session_jobs(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_v2_session_jobs_session
ON v2_session_jobs(session_id, pipeline_version);

CREATE INDEX IF NOT EXISTS idx_v2_session_job_events_job
ON v2_session_job_events(job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_v2_central_merge_plans_job
ON v2_central_merge_plans(job_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_v2_central_merge_plans_status
ON v2_central_merge_plans(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_v2_central_review_candidates_plan
ON v2_central_review_candidates(plan_id, status);

CREATE INDEX IF NOT EXISTS idx_v2_central_decision_frames_repo
ON v2_central_decision_frames(repo_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_v2_semantic_eval_runs_status
ON v2_semantic_eval_runs(status, updated_at DESC);
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


def _rebuild_v2_graph_views_for_repo_scope(conn: sqlite3.Connection) -> None:
    """Replace the old branch-only unique constraint with repo-scoped views."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS v2_graph_views_new (
          view_id TEXT PRIMARY KEY,
          repo_id TEXT NOT NULL DEFAULT '',
          branch TEXT NOT NULL DEFAULT 'main',
          mode TEXT NOT NULL DEFAULT 'active',
          graph_commit_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(repo_id, branch, mode, status)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO v2_graph_views_new(
          view_id, repo_id, branch, mode, graph_commit_id, status,
          metadata_json, created_at, updated_at
        )
        SELECT view_id, COALESCE(repo_id, ''), branch, mode, graph_commit_id, status,
               metadata_json, created_at, updated_at
        FROM v2_graph_views
        """
    )
    conn.execute("DROP TABLE v2_graph_views")
    conn.execute("ALTER TABLE v2_graph_views_new RENAME TO v2_graph_views")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_graph_views_lookup
        ON v2_graph_views(repo_id, branch, mode, status)
        """
    )


def _rebuild_v2_central_merge_locks_for_repo_scope(conn: sqlite3.Connection) -> None:
    """Replace the old branch-only lock key with repo-scoped branch locks."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS v2_central_merge_locks_new (
          repo_id TEXT NOT NULL DEFAULT '',
          branch TEXT NOT NULL DEFAULT 'main',
          lock_owner TEXT NOT NULL DEFAULT '',
          lock_expires_at TEXT NOT NULL DEFAULT '',
          expected_parent_graph_commit_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(repo_id, branch)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO v2_central_merge_locks_new(
          repo_id, branch, lock_owner, lock_expires_at,
          expected_parent_graph_commit_id, created_at, updated_at
        )
        SELECT COALESCE(repo_id, ''), branch, lock_owner, lock_expires_at,
               expected_parent_graph_commit_id, created_at, updated_at
        FROM v2_central_merge_locks
        """
    )
    conn.execute("DROP TABLE v2_central_merge_locks")
    conn.execute("ALTER TABLE v2_central_merge_locks_new RENAME TO v2_central_merge_locks")


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

    _add_column_if_missing(conn, "v2_session_jobs", "last_successful_stage", "last_successful_stage TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_jobs", "source_app", "source_app TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_jobs", "repo_path", "repo_path TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_jobs", "repo_id", "repo_id TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_jobs", "forced_at", "forced_at TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_jobs", "forced_by", "forced_by TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_session_job_stages", "stage_config_hash", "stage_config_hash TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "v2_graph_commits", "repo_id", "repo_id TEXT NOT NULL DEFAULT ''")
    graph_views_had_repo_id = "repo_id" in _existing_columns(conn, "v2_graph_views")
    _add_column_if_missing(conn, "v2_graph_views", "repo_id", "repo_id TEXT NOT NULL DEFAULT ''")
    if not graph_views_had_repo_id:
        _rebuild_v2_graph_views_for_repo_scope(conn)
    locks_had_repo_id = "repo_id" in _existing_columns(conn, "v2_central_merge_locks")
    _add_column_if_missing(conn, "v2_central_merge_locks", "repo_id", "repo_id TEXT NOT NULL DEFAULT ''")
    if not locks_had_repo_id:
        _rebuild_v2_central_merge_locks_for_repo_scope(conn)


def _create_post_migration_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_session_jobs_repo
        ON v2_session_jobs(repo_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_graph_commits_branch
        ON v2_graph_commits(repo_id, branch, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_graph_views_lookup
        ON v2_graph_views(repo_id, branch, mode, status)
        """
    )


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _run_light_migrations(conn)
    _create_post_migration_indexes(conn)
    try:
        conn.executescript(FTS_SQL)
    except sqlite3.OperationalError:
        # Some embedded SQLite builds omit FTS5. Retrieval falls back to LIKE/vector.
        pass
    conn.commit()
