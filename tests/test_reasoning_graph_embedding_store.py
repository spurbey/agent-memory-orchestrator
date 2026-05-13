from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph import GraphEmbeddingRecord
from agent_memory_orchestrator.reasoning_graph import GraphEmbeddingStore
from agent_memory_orchestrator.reasoning_graph import hash_content


def _store(tmp_path: Path) -> GraphEmbeddingStore:
    db_path = tmp_path / "amo.db"
    conn = connect(db_path)
    return GraphEmbeddingStore(conn, db_path=db_path)


def _record(node_id: str, text: str, vector: list[float]) -> GraphEmbeddingRecord:
    return GraphEmbeddingRecord.create(
        node_id=node_id,
        node_kind="DecisionThread",
        memory_class="decision",
        graph_scope="session",
        graph_path="graph.kuzu",
        session_id="s1",
        extraction_run_id="run1",
        embedding_kind="text",
        model="test-model",
        text=text,
        vector=vector,
    )


def test_graph_embedding_store_writes_canonical_sqlite_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record("thread:1", "Create config and database modules.", [1.0, 0.0])

    store.upsert(record)
    rows = store.list_records(embedding_kind="text", model="test-model")

    assert len(rows) == 1
    assert rows[0].embedding_id == record.embedding_id
    assert rows[0].node_id == "thread:1"
    assert rows[0].memory_class == "decision"
    assert rows[0].content_hash == hash_content("Create config and database modules.")
    assert rows[0].vector == [1.0, 0.0]


def test_graph_embedding_search_falls_back_to_sqlite_without_faiss(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_record("thread:config", "Settings config module.", [1.0, 0.0]))
    store.upsert(_record("thread:db", "SQLite database schema.", [0.0, 1.0]))

    hits, backend = store.search([0.95, 0.05], embedding_kind="text", model="test-model", limit=2)

    assert backend == "sqlite:completed"
    assert [hit.node_id for hit in hits] == ["thread:config", "thread:db"]
    assert hits[0].score > hits[1].score


def test_graph_embedding_faiss_cache_is_rebuildable_when_available(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_record("thread:config", "Settings config module.", [1.0, 0.0]))
    store.upsert(_record("thread:db", "SQLite database schema.", [0.0, 1.0]))

    build = store.build_faiss_cache(embedding_kind="text", model="test-model")

    assert build.status in {"completed", "skipped"}
    if build.status == "completed":
        hits, backend = store.search([0.0, 1.0], embedding_kind="text", model="test-model", limit=1, backend="faiss")
        assert backend == "faiss:completed"
        assert hits[0].node_id == "thread:db"
    else:
        assert build.reason.startswith("faiss_unavailable:")
