from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class RawEvidenceRef:
    id: str
    hash: str
    path: str
    offset: int
    session_id: str
    source_app: str
    event_name: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hash": self.hash,
            "path": self.path,
            "offset": self.offset,
            "session_id": self.session_id,
            "source_app": self.source_app,
            "event_name": self.event_name,
            "created_at": self.created_at,
        }


class RawEvidenceStore:
    """Append-only JSONL evidence store.

    Raw hook/transcript payloads live here as evidence. Graph nodes point to
    evidence refs; raw event text is not treated as memory by default.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        source_app: str,
        event_name: str,
    ) -> RawEvidenceRef:
        created_at = datetime.now(timezone.utc).isoformat()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        evidence_id = f"raw_{uuid.uuid4().hex}"
        path = self.root / f"{created_at[:10]}.jsonl"
        record = {
            "id": evidence_id,
            "hash": digest,
            "session_id": session_id,
            "source_app": source_app,
            "event_name": event_name,
            "created_at": created_at,
            "payload": payload,
        }
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            offset = handle.tell()
            handle.write(line)
        return RawEvidenceRef(
            id=evidence_id,
            hash=digest,
            path=str(path.resolve()),
            offset=offset,
            session_id=session_id,
            source_app=source_app,
            event_name=event_name,
            created_at=created_at,
        )

