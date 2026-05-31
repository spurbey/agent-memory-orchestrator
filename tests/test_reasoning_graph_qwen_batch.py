from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_orchestrator.domain.reasoning import BatchQwenDecisionExtractor
from agent_memory_orchestrator.domain.reasoning import DEFAULT_QWEN_BATCH_RUNTIME
from agent_memory_orchestrator.domain.reasoning import DECISION_EXTRACTION_CALL
from agent_memory_orchestrator.domain.reasoning import DecisionThread
from agent_memory_orchestrator.domain.reasoning import ExtractionRun
from agent_memory_orchestrator.domain.reasoning import QwenBatchJob
from agent_memory_orchestrator.domain.reasoning import QwenBatchResult
from agent_memory_orchestrator.domain.reasoning import TimelineEvent
from agent_memory_orchestrator.domain.reasoning import build_decision_extraction_payload
from agent_memory_orchestrator.domain.reasoning import extract_decisions
from agent_memory_orchestrator.domain.reasoning import load_qwen_batch_job
from agent_memory_orchestrator.domain.reasoning import load_qwen_batch_result
from agent_memory_orchestrator.domain.reasoning import validate_qwen_batch_result
from agent_memory_orchestrator.domain.reasoning import write_qwen_batch_job
from agent_memory_orchestrator.domain.reasoning import write_qwen_batch_result


def _event(event_id: str, event_type: str, content: str) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        session_id="s1",
        event_type=event_type,
        timestamp="2026-05-11T00:00:00.000Z",
        content=content,
        files=("src/agent_memory_orchestrator/config.py",),
        evidence_id="raw_qwen_batch",
    )


def _thread() -> DecisionThread:
    return DecisionThread(
        id="thread:s1:qwen-batch",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw_qwen_batch",
        event_ids=("event:user", "event:assistant", "event:tool"),
        topic="AMO config settings module creation",
        file_paths=("src/agent_memory_orchestrator/config.py",),
        evidence_ids=("raw_qwen_batch",),
    )


def _run() -> ExtractionRun:
    return ExtractionRun.create(
        session_id="s1",
        evidence_ids=("raw_qwen_batch",),
        run_id="extraction_run:s1:raw_qwen_batch",
    )


def _events() -> list[TimelineEvent]:
    return [
        _event("event:user", "user_message", "Create the AMO config module."),
        _event(
            "event:assistant",
            "agent_message",
            "The config module should centralize local settings.",
        ),
        _event(
            "event:tool",
            "tool_use",
            "ToolFact: write_patch | Add File: config.py | class Settings",
        ),
    ]


def _valid_decision_output() -> dict[str, object]:
    return {
        "decisions": [
            {
                "decision_type": "completed_fix",
                "subject": "AMO config module",
                "predicate": "centralizes",
                "object": "local runtime settings",
                "reason": "the package needs one deterministic settings loader",
                "confidence": 0.82,
                "evidence_event_ids": ["raw_qwen_batch"],
            }
        ]
    }


def test_qwen_batch_job_round_trips_and_validates(tmp_path: Path) -> None:
    payload = build_decision_extraction_payload(
        thread=_thread(),
        events=_events(),
        extraction_run=_run(),
    )
    job = QwenBatchJob.create(
        call=DECISION_EXTRACTION_CALL,
        payload=payload,
        model="qwen3:1.7b",
    )
    job_path = write_qwen_batch_job(job, tmp_path)
    loaded_job = load_qwen_batch_job(job_path)
    result = QwenBatchResult.create(job=loaded_job, output=_valid_decision_output())
    result_path = write_qwen_batch_result(result, tmp_path)
    loaded_result = load_qwen_batch_result(result_path)

    validation = validate_qwen_batch_result(loaded_job, loaded_result)

    assert validation.ok is True
    assert loaded_job.payload_hash == job.payload_hash
    assert loaded_job.runtime == DEFAULT_QWEN_BATCH_RUNTIME
    assert loaded_result.runtime == DEFAULT_QWEN_BATCH_RUNTIME
    assert loaded_result.output == _valid_decision_output()


def test_qwen_batch_result_rejects_mismatched_payload_hash() -> None:
    payload = build_decision_extraction_payload(
        thread=_thread(),
        events=_events(),
        extraction_run=_run(),
    )
    job = QwenBatchJob.create(call=DECISION_EXTRACTION_CALL, payload=payload)
    result = QwenBatchResult(
        job_id=job.job_id,
        call=job.call,
        payload_hash="wrong-hash",
        output=_valid_decision_output(),
    )

    validation = validate_qwen_batch_result(job, result)

    assert validation.ok is False
    assert "payload_hash_mismatch" in validation.errors


def test_qwen_batch_result_rejects_invalid_decision_schema() -> None:
    payload = build_decision_extraction_payload(
        thread=_thread(),
        events=_events(),
        extraction_run=_run(),
    )
    job = QwenBatchJob.create(call=DECISION_EXTRACTION_CALL, payload=payload)
    result = QwenBatchResult.create(job=job, output={"decisions": [{"subject": "missing fields"}]})

    validation = validate_qwen_batch_result(job, result)

    assert validation.ok is False
    assert any(error.startswith("decision_0_missing:") for error in validation.errors)


def test_batch_qwen_result_feeds_existing_decision_extraction() -> None:
    thread = _thread()
    events = _events()
    run = _run()
    payload = build_decision_extraction_payload(thread=thread, events=events, extraction_run=run)
    job = QwenBatchJob.create(call=DECISION_EXTRACTION_CALL, payload=payload)
    result = QwenBatchResult.create(job=job, output=_valid_decision_output())
    qwen = BatchQwenDecisionExtractor(job=job, result=result)

    extracted = extract_decisions(thread=thread, events=events, extraction_run=run, qwen=qwen)

    assert len(extracted.decisions) == 1
    decision = extracted.decisions[0]
    assert decision.kind == "Fix"
    assert decision.source == "qwen"
    assert decision.qwen_call == DECISION_EXTRACTION_CALL
    assert decision.extraction_run_id == run.id
    assert decision.evidence_ids == ("raw_qwen_batch",)


def test_batch_qwen_result_can_cite_input_transcript_event_ids() -> None:
    events = _events()
    thread = DecisionThread(
        id="thread:s1:transcript-provenance",
        session_id="s1",
        extraction_run_id="extraction_run:s1:transcript",
        event_ids=("event:user", "event:assistant", "event:tool"),
        topic="AMO config settings module creation",
        file_paths=("src/agent_memory_orchestrator/config.py",),
        evidence_ids=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=(), run_id="extraction_run:s1:transcript")
    payload = build_decision_extraction_payload(thread=thread, events=events, extraction_run=run)
    output = _valid_decision_output()
    output["decisions"][0]["evidence_event_ids"] = ["event:tool"]
    job = QwenBatchJob.create(call=DECISION_EXTRACTION_CALL, payload=payload)
    result = QwenBatchResult.create(job=job, output=output)
    qwen = BatchQwenDecisionExtractor(job=job, result=result)

    extracted = extract_decisions(thread=thread, events=events, extraction_run=run, qwen=qwen)

    assert len(extracted.decisions) == 1
    assert extracted.decisions[0].evidence_ids == ("event:tool",)


def test_batch_extractor_refuses_wrong_payload_at_use_time() -> None:
    thread = _thread()
    events = _events()
    run = _run()
    payload = build_decision_extraction_payload(thread=thread, events=events, extraction_run=run)
    job = QwenBatchJob.create(call=DECISION_EXTRACTION_CALL, payload=payload)
    result = QwenBatchResult.create(job=job, output=_valid_decision_output())
    qwen = BatchQwenDecisionExtractor(job=job, result=result)

    with pytest.raises(ValueError, match="qwen_batch_payload_hash_mismatch"):
        qwen.extract({"different": "payload"})
