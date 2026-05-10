from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True, frozen=True)
class VectorRow:
    memory_id: str
    vector: list[float]
    model: str


@dataclass(slots=True, frozen=True)
class FaissBuildResult:
    backend: str
    status: str
    item_count: int
    dims: int
    model: str
    index_path: str
    metadata_path: str
    reason: str = ""


@dataclass(slots=True, frozen=True)
class FaissSearchResult:
    candidates: list[tuple[str, float]]
    backend: str
    status: str
    reason: str = ""


def build_faiss_cache(db_path: Path, rows: Iterable[VectorRow], model: str) -> FaissBuildResult:
    row_list = [row for row in rows if row.vector]
    if not row_list:
        return FaissBuildResult("faiss", "skipped", 0, 0, model, "", "", "no_vectors")
    dims = len(row_list[0].vector)
    compatible = [row for row in row_list if len(row.vector) == dims]
    try:
        import faiss  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        return FaissBuildResult("faiss", "skipped", len(compatible), dims, model, "", "", f"faiss_unavailable:{exc}")

    index_dir = _index_dir(db_path)
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "memory_vectors.faiss"
    metadata_path = index_dir / "memory_vectors.json"

    matrix = np.array([row.vector for row in compatible], dtype="float32")
    faiss.normalize_L2(matrix)
    index = faiss.IndexFlatIP(dims)
    index.add(matrix)
    faiss.write_index(index, str(index_path))
    metadata_path.write_text(
        json.dumps(
            {
                "ids": [row.memory_id for row in compatible],
                "dims": dims,
                "model": model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return FaissBuildResult(
        "faiss",
        "completed",
        len(compatible),
        dims,
        model,
        str(index_path),
        str(metadata_path),
    )


def search_faiss_cache(db_path: Path, query_vector: list[float], limit: int) -> FaissSearchResult:
    index_dir = _index_dir(db_path)
    index_path = index_dir / "memory_vectors.faiss"
    metadata_path = index_dir / "memory_vectors.json"
    if not index_path.exists() or not metadata_path.exists():
        return FaissSearchResult([], "faiss", "skipped", "index_missing")
    try:
        import faiss  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        return FaissSearchResult([], "faiss", "skipped", f"faiss_unavailable:{exc}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    ids = list(metadata.get("ids") or [])
    dims = int(metadata.get("dims") or 0)
    if not query_vector or len(query_vector) != dims:
        return FaissSearchResult([], "faiss", "skipped", "dimension_mismatch")

    index = faiss.read_index(str(index_path))
    query = np.array([query_vector], dtype="float32")
    faiss.normalize_L2(query)
    distances, indices = index.search(query, max(limit, 1))
    candidates: list[tuple[str, float]] = []
    for idx, score in zip(indices[0].tolist(), distances[0].tolist()):
        if idx < 0 or idx >= len(ids):
            continue
        candidates.append((str(ids[idx]), float(score)))
    return FaissSearchResult(candidates, "faiss", "completed")


def _index_dir(db_path: Path) -> Path:
    return db_path.parent / "indexes" / db_path.stem
