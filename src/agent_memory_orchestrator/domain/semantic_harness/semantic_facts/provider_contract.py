from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import ANCHOR_LOCAL_SCOPE
from .models import DERIVABLE_FROM_CURRENT_CODE
from .models import DERIVABLE_FROM_DOCS
from .models import RELATIONSHIP_SCOPE
from .models import REQUIRES_AGENT_SESSION_HISTORY
from .models import REQUIRES_GIT_HISTORY
from .models import REQUIRES_HUMAN_INTENT
from .models import REQUIRES_RUNTIME_OBSERVATION
from .models import SOURCE_AGENT_SESSION
from .models import SOURCE_CURRENT_CODE
from .models import SOURCE_DOCS
from .models import SOURCE_DOCSTRING
from .models import SOURCE_HUMAN_COMMIT
from .models import SOURCE_MANUAL_ANNOTATION
from .models import SOURCE_PULL_REQUEST
from .models import SPAN_COMMIT_MESSAGE
from .models import SPAN_DOC_CLAIM
from .models import SPAN_FINAL_SUMMARY
from .models import SPAN_MANUAL_NOTE
from .models import SPAN_PR_BODY
from .models import SPAN_RUNTIME_OBSERVATION
from .models import SPAN_VALIDATED_COMMITTED
from .models import SYSTEM_SCOPE
from .parser import SUPPORTED_SEMANTIC_FACT_TYPES


REPO_SEMANTIC_FACT_CONTRACT_VERSION = "repo-semantic-fact-proposal-v1"

ALLOWED_DERIVABILITY = (
    DERIVABLE_FROM_CURRENT_CODE,
    DERIVABLE_FROM_DOCS,
    REQUIRES_GIT_HISTORY,
    REQUIRES_AGENT_SESSION_HISTORY,
    REQUIRES_HUMAN_INTENT,
    REQUIRES_RUNTIME_OBSERVATION,
    "mixed",
    "unknown",
)
ALLOWED_SOURCE_KINDS = (
    SOURCE_AGENT_SESSION,
    SOURCE_CURRENT_CODE,
    SOURCE_DOCS,
    SOURCE_DOCSTRING,
    SOURCE_HUMAN_COMMIT,
    SOURCE_MANUAL_ANNOTATION,
    SOURCE_PULL_REQUEST,
    "runtime_observation",
    "imported_history",
)
ALLOWED_SOURCE_SPANS = (
    SPAN_COMMIT_MESSAGE,
    SPAN_DOC_CLAIM,
    SPAN_FINAL_SUMMARY,
    SPAN_MANUAL_NOTE,
    SPAN_PR_BODY,
    SPAN_RUNTIME_OBSERVATION,
    SPAN_VALIDATED_COMMITTED,
)
ALLOWED_FACT_SCOPES = (ANCHOR_LOCAL_SCOPE, RELATIONSHIP_SCOPE, SYSTEM_SCOPE)


def repo_semantic_fact_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_type": {"type": "string", "enum": sorted(SUPPORTED_SEMANTIC_FACT_TYPES)},
                        "text": {"type": "string"},
                        "anchor_node_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "source_refs": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ref_id": {"type": "string"},
                                    "ref_kind": {"type": "string"},
                                    "path": {"type": "string"},
                                    "line": {"type": "integer"},
                                    "node_id": {"type": "string"},
                                    "excerpt": {"type": "string"},
                                },
                                "required": ["ref_id", "ref_kind"],
                                "additionalProperties": False,
                            },
                        },
                        "derivability": {"type": "string", "enum": list(ALLOWED_DERIVABILITY)},
                        "source_kind": {"type": "string", "enum": list(ALLOWED_SOURCE_KINDS)},
                        "source_span": {"type": "string", "enum": list(ALLOWED_SOURCE_SPANS)},
                        "fact_scope": {"type": "string", "enum": list(ALLOWED_FACT_SCOPES)},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "discovery_cost": {"type": "string"},
                        "as_of_commit": {"type": "string"},
                        "verification_status": {"type": "string"},
                    },
                    "required": [
                        "fact_type",
                        "text",
                        "anchor_node_ids",
                        "source_refs",
                        "derivability",
                        "source_kind",
                        "source_span",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    }


def repo_semantic_fact_contract_hash() -> str:
    payload = json.dumps(repo_semantic_fact_output_schema(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"{REPO_SEMANTIC_FACT_CONTRACT_VERSION}:{payload}".encode("utf-8")
    ).hexdigest()


def build_repo_semantic_fact_prompt(packet: dict[str, Any]) -> str:
    packet_json = json.dumps(packet, ensure_ascii=False, indent=2)
    return (
        "You are producing repo-semantic facts for AMO Semantic Harness.\n"
        "Return only a JSON object with key facts. No markdown, no prose outside JSON.\n"
        "Use this contract version: "
        f"{REPO_SEMANTIC_FACT_CONTRACT_VERSION}.\n\n"
        "Hard rules:\n"
        "- Propose SemanticFactProposal items only; do not emit Problem/Decision/Cause/Fix nodes.\n"
        "- Use only anchor_node_ids listed in packet.allowed_anchor_node_ids.\n"
        "- Use only source_refs listed in packet.allowed_source_refs or refs already present in evidence excerpts.\n"
        "- Do not invent node ids, paths, source refs, commits, files, symbols, tests, or evidence ids.\n"
        "- Reject your own generic facts such as 'modified the function' or 'fixed the code'.\n"
        "- Do not turn intermediate hypotheses into facts.\n"
        "- Prefer specific, anchor-bound facts useful for context_for_anchor questions.\n"
        "- If evidence is weak, return an empty facts array.\n\n"
        "Allowed fact_type values: "
        f"{', '.join(sorted(SUPPORTED_SEMANTIC_FACT_TYPES))}.\n"
        "Allowed derivability values: "
        f"{', '.join(ALLOWED_DERIVABILITY)}.\n"
        "Allowed source_kind values: "
        f"{', '.join(ALLOWED_SOURCE_KINDS)}.\n"
        "Allowed source_span values: "
        f"{', '.join(ALLOWED_SOURCE_SPANS)}.\n\n"
        "Required output shape:\n"
        "{\n"
        '  "facts": [\n'
        "    {\n"
        '      "fact_type": "implementation_rationale",\n'
        '      "text": "specific non-generic fact",\n'
        '      "anchor_node_ids": ["one or more ids from packet.allowed_anchor_node_ids"],\n'
        '      "source_refs": [{"ref_id": "ref from packet.allowed_source_refs", "ref_kind": "kind"}],\n'
        '      "derivability": "requires_agent_session_history",\n'
        '      "source_kind": "agent_session",\n'
        '      "source_span": "validated_committed",\n'
        '      "fact_scope": "anchor_local",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Packet:\n"
        f"{packet_json}\n"
    )


__all__ = [
    "ALLOWED_DERIVABILITY",
    "ALLOWED_FACT_SCOPES",
    "ALLOWED_SOURCE_KINDS",
    "ALLOWED_SOURCE_SPANS",
    "REPO_SEMANTIC_FACT_CONTRACT_VERSION",
    "build_repo_semantic_fact_prompt",
    "repo_semantic_fact_contract_hash",
    "repo_semantic_fact_output_schema",
]
