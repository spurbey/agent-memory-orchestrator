from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..models import StructuralHarnessGraph
from .models import SOURCE_AGENT_SESSION
from .models import SPAN_INTERMEDIATE_HYPOTHESIS
from .models import SemanticFactSourceRef


@dataclass(slots=True, frozen=True)
class SemanticEvidencePacket:
    packet_id: str
    repo_id: str
    source_kind: str
    source_span: str
    source_id: str
    anchor_node_ids: tuple[str, ...]
    source_refs: tuple[dict[str, str], ...]
    payload: dict[str, Any]
    existing_graph_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "repo_id": self.repo_id,
            "source_kind": self.source_kind,
            "source_span": self.source_span,
            "source_id": self.source_id,
            "anchor_node_ids": list(self.anchor_node_ids),
            "source_refs": list(self.source_refs),
            "payload": dict(self.payload),
            "existing_graph_refs": list(self.existing_graph_refs),
        }


@dataclass(slots=True, frozen=True)
class SemanticEvidencePacketBuild:
    packet: SemanticEvidencePacket | None
    diagnostics: tuple[dict[str, str], ...]

    @property
    def passed(self) -> bool:
        return self.packet is not None and not any(item.get("level") == "error" for item in self.diagnostics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "packet": self.packet.as_dict() if self.packet is not None else None,
            "diagnostics": list(self.diagnostics),
        }


def build_semantic_evidence_packet(
    graph: StructuralHarnessGraph,
    *,
    source_kind: str,
    source_span: str,
    source_id: str,
    anchor_node_ids: tuple[str, ...],
    source_refs: tuple[SemanticFactSourceRef, ...],
    payload: dict[str, Any],
    existing_graph_refs: tuple[str, ...] = (),
) -> SemanticEvidencePacketBuild:
    diagnostics = _packet_diagnostics(
        graph,
        source_kind=source_kind,
        source_span=source_span,
        source_id=source_id,
        anchor_node_ids=anchor_node_ids,
        source_refs=source_refs,
    )
    if any(item["level"] == "error" for item in diagnostics):
        return SemanticEvidencePacketBuild(packet=None, diagnostics=tuple(diagnostics))
    packet = SemanticEvidencePacket(
        packet_id=_packet_id(
            repo_id=graph.repo_id,
            source_kind=source_kind,
            source_span=source_span,
            source_id=source_id,
            anchor_node_ids=anchor_node_ids,
            source_refs=tuple(ref.as_dict() for ref in source_refs),
        ),
        repo_id=graph.repo_id,
        source_kind=source_kind,
        source_span=source_span,
        source_id=source_id,
        anchor_node_ids=tuple(dict.fromkeys(anchor_node_ids)),
        source_refs=tuple(ref.as_dict() for ref in source_refs),
        payload=dict(payload),
        existing_graph_refs=tuple(dict.fromkeys(existing_graph_refs)),
    )
    return SemanticEvidencePacketBuild(packet=packet, diagnostics=tuple(diagnostics))


def _packet_diagnostics(
    graph: StructuralHarnessGraph,
    *,
    source_kind: str,
    source_span: str,
    source_id: str,
    anchor_node_ids: tuple[str, ...],
    source_refs: tuple[SemanticFactSourceRef, ...],
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if not source_kind.strip():
        diagnostics.append(_diag("error", "missing_source_kind"))
    if not source_span.strip():
        diagnostics.append(_diag("error", "missing_source_span"))
    if not source_id.strip():
        diagnostics.append(_diag("error", "missing_source_id"))
    if not anchor_node_ids:
        diagnostics.append(_diag("error", "missing_anchor_node_ids"))
    if not source_refs:
        diagnostics.append(_diag("error", "missing_source_refs"))
    if source_kind == SOURCE_AGENT_SESSION and source_span == SPAN_INTERMEDIATE_HYPOTHESIS:
        diagnostics.append(_diag("error", "agent_session_intermediate_hypothesis_excluded"))
    node_ids = set(graph.node_by_id())
    missing = tuple(anchor_id for anchor_id in anchor_node_ids if anchor_id not in node_ids)
    if missing:
        diagnostics.append(_diag("error", "missing_anchor_nodes", value=",".join(sorted(missing)[:6])))
    for index, ref in enumerate(source_refs):
        if not ref.ref_id.strip() or not ref.ref_kind.strip():
            diagnostics.append(_diag("error", "invalid_source_ref", value=str(index)))
    return diagnostics


def _packet_id(
    *,
    repo_id: str,
    source_kind: str,
    source_span: str,
    source_id: str,
    anchor_node_ids: tuple[str, ...],
    source_refs: tuple[dict[str, str], ...],
) -> str:
    stable = "\n".join(
        (
            repo_id,
            source_kind,
            source_span,
            source_id,
            ",".join(sorted(anchor_node_ids)),
            repr(tuple(sorted(tuple(sorted(ref.items())) for ref in source_refs))),
        )
    )
    return f"semantic_packet:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


def _diag(level: str, reason: str, *, value: str = "") -> dict[str, str]:
    out = {"level": level, "reason": reason}
    if value:
        out["value"] = value
    return out


__all__ = ["SemanticEvidencePacket", "SemanticEvidencePacketBuild", "build_semantic_evidence_packet"]
