from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace

from .models import REVIEW_REVIEW_ONLY
from .models import STALE_RISK
from .models import SemanticFact


@dataclass(slots=True, frozen=True)
class SemanticFactStalenessResult:
    facts: tuple[SemanticFact, ...]
    stale_fact_ids: tuple[str, ...]
    diagnostics: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "facts": [fact.as_dict() for fact in self.facts],
            "stale_fact_ids": list(self.stale_fact_ids),
            "diagnostics": list(self.diagnostics),
        }


def mark_stale_facts_for_changed_anchors(
    facts: tuple[SemanticFact, ...],
    *,
    changed_anchor_node_ids: tuple[str, ...],
    changed_after_commit: str,
) -> SemanticFactStalenessResult:
    changed = set(changed_anchor_node_ids)
    updated: list[SemanticFact] = []
    stale_ids: list[str] = []
    diagnostics: list[dict[str, str]] = []
    for fact in facts:
        should_mark = (
            bool(fact.verified_against_commit)
            and bool(changed_after_commit)
            and fact.verified_against_commit != changed_after_commit
            and bool(changed & set(fact.anchor_node_ids))
        )
        if not should_mark:
            updated.append(fact)
            continue
        stale = replace(
            fact,
            review_status=REVIEW_REVIEW_ONLY,
            verification_status=STALE_RISK,
        )
        updated.append(stale)
        stale_ids.append(stale.fact_id)
        diagnostics.append(
            {
                "fact_id": stale.fact_id,
                "reason": "verified_fact_touched_after_verification",
                "verified_against_commit": fact.verified_against_commit,
                "changed_after_commit": changed_after_commit,
            }
        )
    return SemanticFactStalenessResult(
        facts=tuple(updated),
        stale_fact_ids=tuple(stale_ids),
        diagnostics=tuple(diagnostics),
    )


__all__ = ["SemanticFactStalenessResult", "mark_stale_facts_for_changed_anchors"]
