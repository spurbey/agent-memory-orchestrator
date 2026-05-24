from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

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
