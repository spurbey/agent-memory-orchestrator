from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_memory_orchestrator.application.services.semantic_harness.enrichment.eval_runner import (
    run_repo_semantic_producer_eval,
)
from agent_memory_orchestrator.application.services.semantic_harness.enrichment.provider import ExternalProviderConfig


def test_repo_semantic_eval_runner_writes_artifacts_without_attaching(tmp_path: Path) -> None:
    job_root = _write_job_fixture(tmp_path / "job")
    out_dir = tmp_path / "out"
    provider = _FakeProvider(
        {
            "facts": [
                {
                    "fact_type": "implementation_rationale",
                    "text": "The connector contract moved into the domain package so domain models no longer depend on integration adapters.",
                    "anchor_node_ids": ["file:repo:production-job:024f41e7fd53:src/agent_memory_orchestrator/domain/connectors/models.py"],
                    "source_refs": [{"ref_id": "E00021", "ref_kind": "rationale_ref"}],
                    "derivability": "requires_agent_session_history",
                    "source_kind": "agent_session",
                    "source_span": "validated_committed",
                    "confidence": 0.82,
                }
            ]
        }
    )

    report = run_repo_semantic_producer_eval(job_root=job_root, out_dir=out_dir, provider=provider)

    assert report.provider_error == ""
    assert len(report.parse.proposals) == 1
    assert len(report.review.accepted_facts) == 1
    assert (out_dir / "repo_semantic_packet.json").exists()
    assert (out_dir / "provider_request_redacted.json").exists()
    assert (out_dir / "comparison_report.json").exists()
    assert "secret-key" not in (out_dir / "provider_request_redacted.json").read_text(encoding="utf-8")


def test_repo_semantic_eval_runner_rejects_generic_provider_fact(tmp_path: Path) -> None:
    job_root = _write_job_fixture(tmp_path / "job")
    out_dir = tmp_path / "out"
    provider = _FakeProvider(
        {
            "facts": [
                {
                    "fact_type": "implementation_rationale",
                    "text": "Updated the function.",
                    "anchor_node_ids": ["file:repo:production-job:024f41e7fd53:src/agent_memory_orchestrator/domain/connectors/models.py"],
                    "source_refs": [{"ref_id": "E00021", "ref_kind": "rationale_ref"}],
                    "derivability": "requires_agent_session_history",
                    "source_kind": "agent_session",
                    "source_span": "validated_committed",
                    "confidence": 0.82,
                }
            ]
        }
    )

    report = run_repo_semantic_producer_eval(job_root=job_root, out_dir=out_dir, provider=provider)

    assert len(report.review.rejected_facts) == 1
    assert any(item["reason"] == "generic_fact_text" for item in report.review.diagnostics)


class _FakeProvider:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.config = ExternalProviderConfig(
            api_key="secret-key",
            model="fake-model",
            model_env_used="mdoel2",
        )

    def generate_json(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        return self.response


def _write_job_fixture(root: Path) -> Path:
    _write_json(
        root / "work_packets" / "reasoning_work_packets.json",
        [
            {
                "packet_id": "WP0001",
                "commit": {
                    "short_sha": "9e8a1b6",
                    "full_sha": "9e8a1b607e9a37f8ff52dc6774370a6786ab7328",
                    "message": "refactor(connectors): make normalized contracts domain-owned",
                    "changed_file_sample": [
                        "src/agent_memory_orchestrator/domain/connectors/models.py",
                    ],
                },
                "problem_refs": [{"ref": "E00002", "excerpt": "Domain packages are only facades."}],
                "rationale_refs": [{"ref": "E00021", "excerpt": "Move contracts into domain-owned models."}],
                "validation_refs": [{"ref": "E00030", "excerpt": "tests/test_connector_boundaries.py passed"}],
            }
        ],
    )
    _write_json(
        root / "qwen_reasoning" / "results.json",
        [
            {
                "packet_id": "WP0001",
                "parsed_output": {
                    "nodes": [
                        {
                            "node_type": "Decision",
                            "statement": "The architecture is not yet fully honest.",
                            "reason": "Supported by rationale refs.",
                        }
                    ]
                },
            }
        ],
    )
    _write_json(
        root / "reasoning_review" / "accepted_reasoning_nodes.json",
        [
            {
                "node_id": "reason:WP0001:9e8a1b6:00",
                "node_type": "Decision",
                "statement": "Move normalized contracts into domain.",
                "reason": "Domain ownership should not depend on integration adapters.",
                "evidence_refs": ["E00021"],
                "source_packet_id": "WP0001",
            }
        ],
    )
    _write_json(
        root / "git_hunks" / "code_hunks.json",
        [
            {
                "hunk_id": "hunk:WP0001:9e8a1b6:0001",
                "packet_id": "WP0001",
                "path": "src/agent_memory_orchestrator/domain/connectors/models.py",
                "new_start": 1,
                "hunk_lines": ["+class ConnectorEvent:"],
            }
        ],
    )
    _write_json(
        root / "ast_code_nodes" / "code_nodes.json",
        [
            {
                "code_node_id": "code:contract",
                "packet_id": "WP0001",
                "path": "src/agent_memory_orchestrator/domain/connectors/models.py",
                "symbol_kind": "class",
                "qualified_name": "ConnectorEvent",
                "line_start": 1,
                "line_end": 4,
                "text_excerpt": "class ConnectorEvent:",
            }
        ],
    )
    _write_json(
        root / "symbol_versions" / "symbol_versions.json",
        {
            "symbols": [],
            "code_versions": [],
            "edges": [],
        },
    )
    _write_json(root / "reasoning_code_links" / "graph_edges.json", [])
    return root


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
