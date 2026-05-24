from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

from .models import CANONICAL_KEY_VERSION


@dataclass(slots=True, frozen=True)
class CanonicalIdentity:
    atom_kind: str
    repo_id: str
    canonical_key: str
    canonical_key_version: int = CANONICAL_KEY_VERSION

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def exact_canonical_key(*, atom_kind: str, repo_id: str, parts: list[str] | tuple[str, ...]) -> str:
    normalized = [str(part or "").strip().replace("\\", "/").lstrip("./") for part in parts]
    return "|".join([atom_kind, repo_id, *normalized])


def atoms_by_canonical_key(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index existing central KnowledgeAtom nodes by canonical key.

    The planner uses this as read-only central state. It avoids creating
    duplicate atoms while still allowing each session to add its own immutable
    KnowledgeVersion provenance.
    """

    rows: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if str(node.get("kind") or "") != "KnowledgeAtom":
            continue
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        canonical_key = str(metadata.get("canonical_key") or "")
        atom_id = str(node.get("id") or "")
        if not canonical_key or not atom_id:
            continue
        rows.setdefault(
            canonical_key,
            {
                "atom_id": atom_id,
                "atom_kind": str(metadata.get("atom_kind") or ""),
                "repo_id": str(metadata.get("repo_id") or ""),
                "canonical_key": canonical_key,
                "canonical_key_version": int(metadata.get("canonical_key_version") or CANONICAL_KEY_VERSION),
                "status": str(node.get("status") or ""),
                "label": str(node.get("label") or ""),
                "source": "central_graph",
            },
        )
    return rows
