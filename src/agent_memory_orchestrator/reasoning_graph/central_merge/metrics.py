from __future__ import annotations


def embedding_coverage_status(*, total_docs: int, embedded_docs: int) -> dict[str, object]:
    total = max(0, int(total_docs))
    embedded = max(0, int(embedded_docs))
    coverage = (embedded / total) if total else 0.0
    if total == 0:
        status = "missing"
    elif embedded >= total:
        status = "ready"
    elif embedded > 0:
        status = "partial"
    else:
        status = "missing"
    return {
        "total_docs": total,
        "embedded_docs": embedded,
        "missing_docs": max(0, total - embedded),
        "coverage_percent": round(coverage * 100, 3),
        "status": status,
    }
