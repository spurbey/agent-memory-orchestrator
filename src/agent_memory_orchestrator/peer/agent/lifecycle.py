from __future__ import annotations

import time
from typing import Any

from .service_utils import _elapsed_ms


def run_discussion(
    service: Any,
    *,
    query: str,
    peer_ids: list[str] | None = None,
    session_id: str = "",
    min_confidence: float | None = None,
    timeout_seconds: float | None = None,
    max_turns: int = 4,
) -> dict[str, Any]:
    """Run one bounded initiator-owned peer-room discussion lifecycle."""
    started = time.monotonic()
    safe_max_turns = max(1, int(max_turns or 1))
    turn_timeout = service._timeout(timeout_seconds)
    trace: list[dict[str, Any]] = []

    first = service.ask(
        query=query,
        peer_ids=peer_ids,
        session_id=session_id,
        min_confidence=min_confidence,
        timeout_seconds=turn_timeout,
        wait_for_response=True,
        planner_managed=True,
    )
    room_id = str(first.get("room_id") or "")
    if not room_id:
        first["lifecycle"] = {
            "automated": True,
            "turn_count": 0,
            "max_turns": safe_max_turns,
            "trace": [{"step": "local_retrieval", "mode": first.get("mode"), "reason": first.get("reason", "")}],
        }
        return first

    turn_count = 1
    trace.append(_turn_trace(turn_count, "initial_request", first))
    while turn_count <= safe_max_turns:
        plan = _plan_next(service, room_id)
        trace.append({"step": "planner", "turn": turn_count, "plan": plan})
        action = str(plan.get("action") or "wait").strip().lower()
        if action == "finalize":
            final = service._finalize_room(room_id, reason="planner_finalized")
            return _discussion_result(
                service,
                room_id,
                started=started,
                trace=trace,
                turn_count=turn_count,
                max_turns=safe_max_turns,
                final=final,
                reason="planner_finalized",
            )
        if turn_count >= safe_max_turns:
            final = service._finalize_room(room_id, reason="max_turns_reached")
            trace.append({"step": "bounded_stop", "turn": turn_count, "reason": "max_turns_reached"})
            return _discussion_result(
                service,
                room_id,
                started=started,
                trace=trace,
                turn_count=turn_count,
                max_turns=safe_max_turns,
                final=final,
                reason="max_turns_reached",
            )
        if action in {"ask_peer", "ask_peers"}:
            followup_query = str(plan.get("query") or "").strip()
            if not followup_query:
                final = service._finalize_room(room_id, reason="planner_missing_followup_query")
                trace.append({"step": "planner_error", "turn": turn_count, "reason": "planner_missing_followup_query"})
                return _discussion_result(
                    service,
                    room_id,
                    started=started,
                    trace=trace,
                    turn_count=turn_count,
                    max_turns=safe_max_turns,
                    final=final,
                    reason="planner_missing_followup_query",
                )
            selected_peers = [str(item).strip() for item in plan.get("peer_ids", []) if str(item).strip()]
            followup = service.ask_room(
                room_id=room_id,
                query=followup_query,
                peer_ids=selected_peers or None,
                session_id=session_id,
                min_confidence=min_confidence,
                timeout_seconds=turn_timeout,
                reason="planner_followup",
                wait_for_response=True,
                planner_managed=True,
            )
            turn_count += 1
            trace.append(_turn_trace(turn_count, "planner_followup", followup))
            continue
        wait_result = _wait_or_stop(service, room_id, timeout_seconds=turn_timeout)
        trace.append({"step": "wait", "turn": turn_count, "result": wait_result})
        if not wait_result.get("continued"):
            final = service._finalize_room(room_id, reason=str(wait_result.get("reason") or "planner_wait_stopped"))
            return _discussion_result(
                service,
                room_id,
                started=started,
                trace=trace,
                turn_count=turn_count,
                max_turns=safe_max_turns,
                final=final,
                reason=str(wait_result.get("reason") or "planner_wait_stopped"),
            )
    final = service._finalize_room(room_id, reason="max_turns_reached")
    return _discussion_result(
        service,
        room_id,
        started=started,
        trace=trace,
        turn_count=turn_count,
        max_turns=safe_max_turns,
        final=final,
        reason="max_turns_reached",
    )


def _plan_next(service: Any, room_id: str) -> dict[str, Any]:
    config = service.peer.store.load_config()
    state = service.state.load(room_id)
    context = service.peer.store.context_pack(room_id, viewer_node_id=config.node_id)
    responses = service._peer_responses(room_id)
    plan = service._plan_room_continuation(context=context, responses=responses, state=state)
    service._record_planner_action(room_id, plan)
    return plan


def _wait_or_stop(service: Any, room_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    state = service.state.load(room_id)
    active_request_ids = [str(item).strip() for item in state.get("active_request_ids", []) if str(item).strip()]
    if not active_request_ids:
        return {"continued": False, "reason": "planner_wait_without_active_request"}
    responses = service._wait_for_request_responses(room_id, request_ids=active_request_ids, timeout_seconds=timeout_seconds)
    maintenance = service._process_initiator_room_locked(room_id, timeout_seconds=1.0)
    return {
        "continued": bool(responses),
        "reason": "responses_received" if responses else "active_request_timeout",
        "response_count": len(responses),
        "maintenance": maintenance,
    }


def _turn_trace(turn: int, step: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": step,
        "turn": turn,
        "room_id": result.get("room_id", ""),
        "mode": result.get("mode", ""),
        "reason": result.get("reason", ""),
        "response_count": int(result.get("response_count") or len(result.get("peer_responses") or [])),
        "peer_requests": [
            {
                "peer_id": item.get("peer_id", ""),
                "request_id": item.get("request_id", ""),
                "logical_request_id": item.get("logical_request_id", ""),
                "delivery_ok": bool(item.get("delivery_ok")),
            }
            for item in result.get("peer_requests", [])
            if isinstance(item, dict)
        ],
        "timing": result.get("timing", {}),
    }


def _discussion_result(
    service: Any,
    room_id: str,
    *,
    started: float,
    trace: list[dict[str, Any]],
    turn_count: int,
    max_turns: int,
    final: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    result = service._room_result(room_id, mode="peer_discussion", reason=reason, started=started)
    result["mode"] = str((final.get("final") or {}).get("mode") or result.get("mode") or "peer_discussion")
    result["lifecycle"] = {
        "automated": True,
        "turn_count": turn_count,
        "max_turns": max_turns,
        "reason": reason,
        "trace": trace,
        "timing": {"total_ms": _elapsed_ms(started)},
    }
    return result
