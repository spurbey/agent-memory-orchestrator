from __future__ import annotations

import hashlib
import json
from typing import Any


STAGE4_CONTRACT_VERSION = "stage4-reset-2026-05-14"

STAGE4_CONTRACT: dict[str, Any] = {
    "stage": "04_reasoning_node_extraction_sample",
    "input": "Stage 3B reasoning work packets",
    "purpose": "Promote packet evidence into answer-grade reasoning graph nodes.",
    "allowed_node_types": [
        "Problem",
        "Decision",
        "Cause",
        "Fix",
        "Constraint",
        "OpenQuestion",
    ],
    "allowed_edge_types": [
        "DERIVED_FROM_PACKET",
        "EXPLAINS_COMMIT",
        "CAUSED_DECISION",
        "CONSTRAINS_DECISION",
        "ADDRESSED_BY_FIX",
        "VALIDATED_BY",
        "SUPPORTS",
    ],
    "node_schema": {
        "node_id": "stable stage-local id",
        "packet_id": "source packet id",
        "commit_sha": "linked short commit sha",
        "node_type": "Problem|Decision|Cause|Fix|Constraint|OpenQuestion",
        "subject": "short noun phrase",
        "statement": "single factual claim extracted from packet evidence",
        "reason": "why this node should exist in the graph",
        "confidence": "0.0-1.0",
        "evidence_refs": "short refs from the packet only",
        "status": "accepted|needs_review|rejected",
    },
    "hard_rules": [
        "Do not cite raw transcript ids or tool call ids.",
        "Every evidence_ref must exist in the source packet problem_refs, rationale_refs, or validation_refs.",
        "Support refs are provenance only and cannot be cited as reasoning evidence in this stage.",
        "If the packet does not contain enough evidence, emit no node or mark needs_review.",
        "Each node statement must be one claim, not a mixed paragraph.",
        "Every accepted node must link to exactly one packet and one commit.",
    ],
}


def stage4_contract_hash() -> str:
    payload = {
        "version": STAGE4_CONTRACT_VERSION,
        "contract": STAGE4_CONTRACT,
        "output_schema": stage4_output_schema(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_stage4_packet_prompt(packet: dict[str, Any]) -> str:
    return "\n".join(
        [
            "/no_think",
            "You are extracting answer-grade reasoning graph nodes from one commit-backed work packet.",
            "Return JSON only. Do not include markdown fences.",
            "",
            "Stage 4 contract:",
            json.dumps(STAGE4_CONTRACT, ensure_ascii=False, indent=2),
            "",
            "Output shape:",
            json.dumps(_prompt_output_shape(), ensure_ascii=False, separators=(",", ":")),
            "",
            "Input packet:",
            json.dumps(packet, ensure_ascii=False, indent=2),
        ]
    )


def stage4_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "packet_id": {"type": "string"},
            "commit_sha": {"type": "string"},
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "node_type": {"type": "string"},
                        "subject": {"type": "string"},
                        "statement": {"type": "string"},
                        "reason": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "status": {"type": "string"},
                    },
                    "required": [
                        "node_type",
                        "subject",
                        "statement",
                        "reason",
                        "confidence",
                        "evidence_refs",
                        "status",
                    ],
                },
            },
        },
        "required": ["packet_id", "commit_sha", "nodes"],
    }


def _prompt_output_shape() -> dict[str, Any]:
    return {
        "packet_id": "WP0001",
        "commit_sha": "abc1234",
        "nodes": [
            {
                "node_type": "Decision",
                "subject": "...",
                "statement": "...",
                "reason": "...",
                "confidence": 0.0,
                "evidence_refs": ["E00001"],
                "status": "accepted",
            }
        ],
    }


__all__ = [
    "STAGE4_CONTRACT",
    "STAGE4_CONTRACT_VERSION",
    "build_stage4_packet_prompt",
    "stage4_contract_hash",
    "stage4_output_schema",
]
