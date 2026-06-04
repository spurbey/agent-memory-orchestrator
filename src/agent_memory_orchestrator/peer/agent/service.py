from __future__ import annotations

import time
from typing import Any

from ...core.config import Settings
from ...application.services.memory_graph.service import GraphRagService
from ..models import PeerNode
from ..service import PeerService
from .llm import PeerAgentLlmGateway
from .quality import AnswerQuality, AnswerQualityEvaluator
from .responses import best_finalizable_response
from .responses import best_response
from .responses import peer_responses
from .selection import select_context_peers
from .schemas import CONTEXT_REQUEST, CONTEXT_RESPONSE, FINAL_SYNTHESIS
from .schemas import RESPONSE_LLM_ANSWER, RESPONSE_LOW_CONFIDENCE, RESPONSE_NEEDS_APPROVAL, RESPONSE_RETRIEVAL_BUNDLE
from .schemas import citation_strings, compact_retrieval_bundle, redacted_answer_text, redacted_retrieval_bundle, stable_json_hash, support_from_retrieval
from .state import PeerAgentStateStore, utc_now
from .service_utils import _answer_text
from .service_utils import _clamp_float
from .service_utils import _deadline_at
from .service_utils import _deadline_expired
from .service_utils import _deterministic_summary
from .service_utils import _elapsed_ms
from .service_utils import _has_verified_transport
from .service_utils import _require_text
from .service_utils import _retrieval_intent
from .service_utils import _retrieval_only_answer
from .service_utils import _room_summary
from .service_utils import _supports_from_responses
from .service_utils import _targets_peer


class PeerAgentService:
    def __init__(
        self,
        settings: Settings,
        *,
        peer_service: PeerService | None = None,
        graph: Any | None = None,
        llm: Any | None = None,
        quality: AnswerQualityEvaluator | None = None,
        state_store: PeerAgentStateStore | None = None,
    ) -> None:
        self.settings = settings
        self.peer = peer_service or PeerService(settings)
        self.graph = graph
        self.llm = llm or PeerAgentLlmGateway(settings)
        self.quality = quality or AnswerQualityEvaluator()
        self.state = state_store or PeerAgentStateStore(self.peer.store)

    def ask(
        self,
        *,
        query: str,
        peer_ids: list[str] | None = None,
        session_id: str = "",
        min_confidence: float | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        safe_query = _require_text(query, "query")
        threshold = self._min_confidence(min_confidence)
        started = time.monotonic()
        local_result = self._retrieve(safe_query, session_id=session_id)
        local_quality = self.quality.evaluate(local_result, query=safe_query, min_confidence=threshold)
        local_support = support_from_retrieval(
            local_result,
            source_peer=self.peer.store.load_config().node_id,
            max_items=8,
        )
        if local_quality.answer_grade:
            return {
                "ok": True,
                "mode": "local_only",
                "answer": _answer_text(local_result),
                "room_id": "",
                "local_quality": local_quality.as_dict(),
                "peer_responses": [],
                "citations": local_support,
                "timing": {"total_ms": _elapsed_ms(started)},
            }

        peers = self._select_peers(peer_ids=peer_ids)
        if not peers:
            return self._retrieval_only_result(
                query=safe_query,
                local_result=local_result,
                local_quality=local_quality,
                room_id="",
                reason="no_configured_peers",
                started=started,
            )

        timeout = self._timeout(timeout_seconds)
        deadline_at = _deadline_at(timeout)
        room_result = self.peer.open_room(topic=safe_query, peer_ids=[peer.node_id for peer in peers], send_invites=True)
        room = room_result.get("room") if isinstance(room_result.get("room"), dict) else {}
        room_id = str(room.get("room_id") or "")
        state = self.state.load(room_id)
        state.update(
            {
                "schema_version": 1,
                "agent_managed": True,
                "status": "open",
                "original_query": safe_query,
                "session_id": session_id,
                "local_retrieval": compact_retrieval_bundle(local_result),
                "local_quality": local_quality.as_dict(),
                "deadline_at": deadline_at,
                "peer_requests": [],
            }
        )
        deliveries = []
        for peer in peers:
            request_id = f"req_{stable_json_hash({'room_id': room_id, 'peer': peer.node_id, 'query': safe_query})[:16]}"
            metadata = {
                "schema_version": 1,
                "agent_room_schema_version": 1,
                "request_id": request_id,
                "room_id": room_id,
                "parent_message_id": "",
                "audience": "peer",
                "target_peer_id": peer.node_id,
                "query": safe_query,
                "session_id": session_id,
                "intent": _retrieval_intent(local_result),
                "open_gaps": local_quality.gaps,
                "min_confidence": threshold,
                "deadline_at": deadline_at,
                "requested_capabilities": ["graph_retrieval", "memory_search", "llm_answer"],
                "disclosure_boundary": room.get("share_boundary") or self.peer.store.load_config().share_boundary(),
                "raw_evidence_requested": False,
            }
            delivery = self.peer.send_message_to_peer(
                peer_id=peer.node_id,
                room_id=room_id,
                message_type=CONTEXT_REQUEST,
                content=safe_query,
                metadata=metadata,
            )
            deliveries.append(delivery)
            state["peer_requests"].append(
                {
                    "peer_id": peer.node_id,
                    "request_id": request_id,
                    "delivery_ok": bool(delivery.get("ok")),
                    "delivery": delivery.get("delivery", {}),
                }
            )
        self.state.save(room_id, state)
        if timeout <= 0:
            return self._room_result(
                room_id,
                mode="timed_out",
                reason="timeout_zero_after_room_creation",
                started=started,
            )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.watch_once(limit=20)
            current = self.state.load(room_id)
            if current.get("status") == "finalized":
                return self._room_result(room_id, mode="peer_assisted", reason="finalized", started=started)
            time.sleep(0.5)

        current = self.state.load(room_id)
        current["status"] = "timed_out"
        current["finalized_reason"] = "deadline_reached"
        self.state.save(room_id, current)
        if self._peer_responses(room_id):
            self._finalize_room(room_id, reason="timeout_with_partial_peer_responses")
            return self._room_result(room_id, mode="peer_assisted", reason="timeout_partial", started=started)
        return self._room_result(room_id, mode="timed_out", reason="deadline_reached", started=started)

    def watch_once(self, *, limit: int | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        config = self.peer.store.load_config()
        drained: dict[str, Any] | None = None
        drain_error = ""
        try:
            drained = self.peer.process_netd_inbox(limit=limit)
        except Exception as exc:  # transport may be offline; local transcript processing can still proceed.
            drain_error = str(exc)
        processed: list[dict[str, Any]] = []
        for room in self.peer.store.list_rooms():
            room_id = str(room.get("room_id") or "")
            if not room_id:
                continue
            with self.state.room_lock(room_id) as lock_acquired:
                if not lock_acquired:
                    processed.append({"room_id": room_id, "ok": True, "skipped": True, "reason": "room_locked"})
                    continue
                try:
                    detail = self.peer.store.get_room(room_id)
                    if str(detail.get("initiator_node_id") or "") == config.node_id:
                        processed.extend(self._process_initiator_room(detail))
                    else:
                        processed.extend(self._process_peer_room(detail))
                except Exception as exc:
                    state = self.state.load(room_id)
                    state["last_error"] = str(exc)
                    self.state.save(room_id, state)
                    processed.append({"room_id": room_id, "ok": False, "error": str(exc)})
        return {
            "ok": not drain_error,
            "netd": drained or {"ok": False, "error": drain_error},
            "processed": processed,
            "processed_count": len(processed),
        }

    def watch_forever(
        self,
        *,
        interval_seconds: float = 2.0,
        max_iterations: int = 0,
        fail_fast: bool = False,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        iterations = 0
        while True:
            result = self.watch_once()
            if fail_fast and not result.get("ok"):
                raise RuntimeError(str(result.get("netd", {}).get("error") or "peer-agent watch failed"))
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return
            time.sleep(interval_seconds)

    def status(self, room_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        room = self.peer.store.get_room(room_id)
        return {"ok": True, "room": _room_summary(room), "agent_state": self.state.load(room_id)}

    def context(self, room_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        config = self.peer.store.load_config()
        return {"ok": True, "context": self.peer.store.context_pack(room_id, viewer_node_id=config.node_id)}

    def messages(self, room_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        room = self.peer.store.get_room(room_id)
        return {"ok": True, "room_id": room_id, "messages": room.get("messages", [])}

    def summarize(self, room_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        context = self.peer.store.context_pack(room_id)
        try:
            payload = self.llm.summarize_room(room_context=context)
            summary_md = str(payload.get("summary_md") or "").strip()
        except Exception:
            summary_md = _deterministic_summary(context)
        if not summary_md:
            summary_md = _deterministic_summary(context)
        updated = self.peer.update_summary(room_id, summary_md=summary_md)
        state = self.state.load(room_id)
        summary_state = state.get("summary") if isinstance(state.get("summary"), dict) else {}
        summary_state["summary_version"] = int(summary_state.get("summary_version") or 0) + 1
        summary_state["last_summary_at"] = utc_now()
        state["summary"] = summary_state
        self.state.save(room_id, state)
        return {"ok": True, "room_id": room_id, "summary_md": summary_md, "update": updated}

    def _process_peer_room(self, room: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.peer.store.load_config()
        room_id = str(room.get("room_id") or "")
        state = self.state.load(room_id)
        results: list[dict[str, Any]] = []
        if str(state.get("status") or "open") in {"finalized", "timed_out", "closed"}:
            return results
        for message in room.get("messages", []):
            if str(message.get("type") or "") != CONTEXT_REQUEST:
                continue
            message_id = str(message.get("message_id") or "")
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            request_id = str(metadata.get("request_id") or message_id)
            if self.state.has_sent_response(state, request_id):
                continue
            if str(message.get("from_node_id") or message.get("from") or "") == config.node_id:
                continue
            if not _targets_peer(message, metadata, config.node_id):
                results.append(
                    {
                        "ok": True,
                        "room_id": room_id,
                        "request_id": request_id,
                        "skipped": True,
                        "reason": "request_not_tagged_for_peer",
                    }
                )
                continue
            gate = self._validate_context_request(room=room, message=message, metadata=metadata)
            if not gate.get("ok"):
                results.append(gate)
                continue
            result = self._respond_to_request(room=room, message=message, metadata=metadata, request_id=request_id)
            state = self.state.mark_processed_message(room_id, message_id)
            state = self.state.mark_processed_request(room_id, request_id)
            state = self.state.record_response_attempt(room_id, request_id, result)
            if result.get("ok"):
                state = self.state.mark_response_sent(room_id, request_id)
            results.append(result)
        return results

    def _respond_to_request(
        self,
        *,
        room: dict[str, Any],
        message: dict[str, Any],
        metadata: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        config = self.peer.store.load_config()
        room_id = str(room.get("room_id") or "")
        initiator = str(message.get("from_node_id") or message.get("from") or room.get("initiator_node_id") or "")
        query = str(metadata.get("query") or message.get("content") or room.get("topic") or "")
        threshold = self._min_confidence(metadata.get("min_confidence"))
        if not config.share_summaries:
            return self._send_response(
                peer_id=initiator,
                room_id=room_id,
                request_id=request_id,
                parent_message_id=str(message.get("message_id") or ""),
                mode=RESPONSE_NEEDS_APPROVAL,
                content="Policy does not allow sharing memory summaries from this peer.",
                confidence=0.0,
                answer_grade=False,
                quality={},
                support=[],
                retrieval_bundle={},
            )
        if metadata.get("raw_evidence_requested") and config.raw_evidence != "allowed":
            return self._send_response(
                peer_id=initiator,
                room_id=room_id,
                request_id=request_id,
                parent_message_id=str(message.get("message_id") or ""),
                mode=RESPONSE_NEEDS_APPROVAL,
                content="Policy requires approval before sharing raw evidence.",
                confidence=0.0,
                answer_grade=False,
                quality={},
                support=[],
                retrieval_bundle={},
            )
        retrieval = self._retrieve(query, session_id=str(metadata.get("session_id") or ""))
        quality = self.quality.evaluate(retrieval, query=query, min_confidence=threshold)
        support = support_from_retrieval(
            retrieval,
            source_peer=config.node_id,
            include_local_refs=config.share_citations,
        )
        bundle = redacted_retrieval_bundle(
            retrieval,
            support=support,
            include_answer_text=config.share_summaries,
            include_local_refs=config.share_citations,
        )
        mode = RESPONSE_LOW_CONFIDENCE
        content = redacted_answer_text(
            _answer_text(retrieval),
            support=support,
            include_local_refs=config.share_citations,
        )
        answer_grade = quality.answer_grade
        confidence = quality.confidence
        if support or quality.confidence >= threshold:
            try:
                llm_payload = self.llm.generate_peer_answer(
                    query=query,
                    retrieval_bundle=bundle,
                    quality=quality.as_dict(),
                    room_context=self.peer.store.context_pack(room_id, viewer_node_id=config.node_id),
                )
                content = redacted_answer_text(
                    str(llm_payload.get("answer") or content).strip() or content,
                    support=support,
                    include_local_refs=config.share_citations,
                )
                confidence = _clamp_float(llm_payload.get("confidence"), default=confidence)
                answer_grade = bool(llm_payload.get("answer_grade", answer_grade)) and bool(support)
                mode = RESPONSE_LLM_ANSWER
            except Exception:
                if self.settings.peer_agent_allow_retrieval_only_responses:
                    mode = RESPONSE_RETRIEVAL_BUNDLE
                    content = content or "Retrieval bundle attached."
                else:
                    mode = RESPONSE_LOW_CONFIDENCE
        return self._send_response(
            peer_id=initiator,
            room_id=room_id,
            request_id=request_id,
            parent_message_id=str(message.get("message_id") or ""),
            mode=mode,
            content=content,
            confidence=confidence,
            answer_grade=answer_grade,
            quality=quality.as_dict(),
            support=support,
            retrieval_bundle=bundle,
        )

    def _send_response(
        self,
        *,
        peer_id: str,
        room_id: str,
        request_id: str,
        parent_message_id: str,
        mode: str,
        content: str,
        confidence: float,
        answer_grade: bool,
        quality: dict[str, Any],
        support: list[dict[str, Any]],
        retrieval_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            "schema_version": 1,
            "request_id": request_id,
            "parent_message_id": parent_message_id,
            "audience": "initiator",
            "target_peer_id": peer_id,
            "mode": mode,
            "answer_grade": bool(answer_grade),
            "finalizable": bool(str(content or "").strip() or support or retrieval_bundle),
            "quality": quality,
            "support": support,
            "retrieval_bundle": retrieval_bundle,
            "response_hash": stable_json_hash(
                {
                    "request_id": request_id,
                    "mode": mode,
                    "content": content,
                    "support": support,
                }
            ),
        }
        citations = citation_strings(support)
        if self.peer.store.load_config().peer_by_id(peer_id) is None:
            return {
                "ok": False,
                "mode": mode,
                "message": None,
                "pending_response": {
                    "peer_id": peer_id,
                    "room_id": room_id,
                    "type": CONTEXT_RESPONSE,
                    "content": content,
                    "citations": citations,
                    "confidence": confidence,
                    "metadata": metadata,
                },
                "delivery": {"ok": False, "error": "peer_not_configured"},
            }
        delivery = self.peer.send_message_to_peer(
            peer_id=peer_id,
            room_id=room_id,
            message_type=CONTEXT_RESPONSE,
            content=content,
            citations=citations,
            confidence=confidence,
            metadata=metadata,
            append_on_success_only=True,
        )
        return {"ok": bool(delivery.get("ok")), "mode": mode, "message": delivery.get("message"), "delivery": delivery.get("delivery")}

    def _process_initiator_room(self, room: dict[str, Any]) -> list[dict[str, Any]]:
        room_id = str(room.get("room_id") or "")
        state = self.state.load(room_id)
        if not state.get("agent_managed"):
            return []
        if state.get("status") == "finalized":
            return []
        results: list[dict[str, Any]] = []
        if _deadline_expired(str(state.get("deadline_at") or "")):
            self._finalize_room(room_id, reason="deadline_reached", lock_held=True)
            return [{"ok": True, "room_id": room_id, "finalized": True, "reason": "deadline_reached"}]
        for message in room.get("messages", []):
            if str(message.get("type") or "") != CONTEXT_RESPONSE:
                continue
            message_id = str(message.get("message_id") or "")
            if self.state.has_processed_message(state, message_id):
                continue
            state = self.state.mark_processed_message(room_id, message_id)
            results.append({"ok": True, "room_id": room_id, "message_id": message_id, "type": CONTEXT_RESPONSE})
        summary_result = self._maybe_summarize_initiator_room(room)
        if summary_result:
            results.append(summary_result)
        if best_finalizable_response(
            self._peer_responses(room_id),
            strong_confidence=self.settings.peer_agent_strong_confidence,
        ) is not None:
            self._finalize_room(room_id, reason="first_peer_response", lock_held=True)
            results.append({"ok": True, "room_id": room_id, "finalized": True})
        return results

    def _maybe_summarize_initiator_room(self, room: dict[str, Any]) -> dict[str, Any] | None:
        room_id = str(room.get("room_id") or "")
        state = self.state.load(room_id)
        if state.get("status") == "finalized":
            return None
        messages = [
            message
            for message in room.get("messages", [])
            if str(message.get("type") or "") not in {"room_created", "room_invite_received", "summary_update"}
        ]
        if not messages:
            return None
        last_message_id = str(messages[-1].get("message_id") or "")
        summary_state = state.get("summary") if isinstance(state.get("summary"), dict) else {}
        if last_message_id and summary_state.get("summarized_until_message_id") == last_message_id:
            return None
        total_chars = sum(len(str(message.get("content") or "")) for message in messages)
        if total_chars < self.settings.peer_agent_summary_token_limit * 4:
            return None
        result = self.summarize(room_id)
        state = self.state.load(room_id)
        summary_state = state.get("summary") if isinstance(state.get("summary"), dict) else {}
        summary_state["summarized_until_message_id"] = last_message_id
        state["summary"] = summary_state
        self.state.save(room_id, state)
        return {"ok": True, "room_id": room_id, "summary_updated": True, "summary": result}

    def _finalize_room(self, room_id: str, *, reason: str, lock_held: bool = False) -> dict[str, Any]:
        if not lock_held:
            with self.state.room_lock(room_id) as lock_acquired:
                if not lock_acquired:
                    return {"ok": True, "room_id": room_id, "skipped": True, "reason": "room_locked"}
                return self._finalize_room(room_id, reason=reason, lock_held=True)
        state = self.state.load(room_id)
        if state.get("status") == "finalized":
            return {"ok": True, "room_id": room_id, "already_finalized": True, "final": state.get("final", {})}
        responses = self._peer_responses(room_id)
        best = best_response(responses)
        local_result = state.get("local_retrieval") if isinstance(state.get("local_retrieval"), dict) else {}
        answer = str((best or {}).get("content") or "")
        mode = "peer_assisted" if best else "retrieval_only"
        confidence = _clamp_float((best or {}).get("confidence"), default=0.0)
        if reason != "first_peer_response":
            try:
                payload = self.llm.synthesize_final(
                    query=str(state.get("original_query") or ""),
                    local_result=local_result,
                    peer_responses=responses,
                    allow_provider=True,
                )
                answer = str(payload.get("answer") or answer).strip() or answer
                confidence = _clamp_float(payload.get("confidence"), default=confidence)
                mode = str(payload.get("mode") or mode)
            except Exception:
                if best and best.get("mode") == RESPONSE_RETRIEVAL_BUNDLE:
                    mode = "retrieval_only"
                if not answer:
                    answer = _retrieval_only_answer(local_result, responses)
                    mode = "retrieval_only"
        elif best:
            mode = str(best.get("mode") or mode)
        if not answer:
            answer = _retrieval_only_answer(local_result, responses)
            mode = "retrieval_only"
        final = {
            "answer": answer,
            "mode": mode,
            "confidence": confidence,
            "reason": reason,
            "peer_responses": responses,
            "citations": _supports_from_responses(responses),
            "created_at": utc_now(),
        }
        state["status"] = "finalized"
        state["finalized_reason"] = reason
        state["final"] = final
        self.state.save(room_id, state)
        self.peer.append_message(
            room_id=room_id,
            from_node_id=self.peer.store.load_config().node_id,
            to_node_ids=[],
            message_type=FINAL_SYNTHESIS,
            content=answer,
            citations=citation_strings(final["citations"]),
            confidence=confidence,
            metadata={"mode": mode, "audience": "local", "local_only": True, "reason": reason, "support": final["citations"]},
        )
        return {"ok": True, "room_id": room_id, "final": final}

    def _room_result(self, room_id: str, *, mode: str, reason: str, started: float) -> dict[str, Any]:
        state = self.state.load(room_id)
        final = state.get("final") if isinstance(state.get("final"), dict) else {}
        responses = self._peer_responses(room_id)
        return {
            "ok": True,
            "mode": str(final.get("mode") or mode),
            "answer": str(final.get("answer") or _retrieval_only_answer(state.get("local_retrieval"), responses)),
            "room_id": room_id,
            "local_quality": state.get("local_quality", {}),
            "peer_responses": responses,
            "citations": final.get("citations") or _supports_from_responses(responses),
            "timing": {"total_ms": _elapsed_ms(started)},
            "reason": str(final.get("reason") or reason),
        }

    def _retrieval_only_result(
        self,
        *,
        query: str,
        local_result: dict[str, Any],
        local_quality: AnswerQuality,
        room_id: str,
        reason: str,
        started: float,
    ) -> dict[str, Any]:
        del query
        return {
            "ok": True,
            "mode": "retrieval_only",
            "answer": _answer_text(local_result),
            "room_id": room_id,
            "local_quality": local_quality.as_dict(),
            "peer_responses": [],
            "citations": support_from_retrieval(local_result, source_peer=self.peer.store.load_config().node_id),
            "timing": {"total_ms": _elapsed_ms(started)},
            "reason": reason,
        }

    def _retrieve(self, query: str, *, session_id: str = "") -> dict[str, Any]:
        try:
            graph = self.graph
            close_after = False
            if graph is None:
                graph = GraphRagService(self.settings, read_only=True)
                close_after = True
            try:
                return graph.retrieve_indexed_graph(
                    query=query,
                    session_id=session_id,
                    limit=8,
                    use_vector=True,
                    require_vector=False,
                    include_answer=True,
                )
            finally:
                if close_after and hasattr(graph, "close"):
                    graph.close()
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "retrieval": {"hits": [], "vector_status": "error", "intent": ""},
                "answer": {"text": "", "citations": [], "node_ids": []},
            }

    def _select_peers(self, *, peer_ids: list[str] | None = None) -> list[PeerNode]:
        config = self.peer.store.load_config()
        return select_context_peers(config, peer_ids=peer_ids, max_peers=self.settings.peer_agent_max_peers)

    def _validate_context_request(
        self,
        *,
        room: dict[str, Any],
        message: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        room_id = str(room.get("room_id") or "")
        request_id = str(metadata.get("request_id") or message.get("message_id") or "")
        if int(metadata.get("schema_version") or 0) != 1:
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "invalid_schema_version"}
        if int(metadata.get("agent_room_schema_version") or 0) != 1:
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "invalid_agent_room_schema_version"}
        if not request_id:
            return {"ok": False, "room_id": room_id, "skipped": True, "reason": "request_id_required"}
        if _deadline_expired(str(metadata.get("deadline_at") or "")):
            state = self.state.load(room_id)
            state["status"] = "timed_out"
            state["finalized_reason"] = "request_deadline_expired"
            self.state.save(room_id, state)
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "request_deadline_expired"}
        sender = str(message.get("from_node_id") or message.get("from") or "")
        if sender != str(room.get("initiator_node_id") or ""):
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "request_sender_not_initiator"}
        participants = {str(item) for item in room.get("participants", []) if str(item).strip()}
        if sender not in participants or self.peer.store.load_config().node_id not in participants:
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "request_participant_mismatch"}
        state = self.state.load(room_id)
        if not state.get("agent_managed") and not _has_verified_transport(metadata):
            return {"ok": False, "room_id": room_id, "request_id": request_id, "skipped": True, "reason": "missing_verified_transport"}
        if not state.get("agent_managed"):
            state["agent_managed"] = True
            state["schema_version"] = 1
            state["status"] = "open"
            state["deadline_at"] = str(metadata.get("deadline_at") or "")
            state["original_query"] = str(metadata.get("query") or message.get("content") or room.get("topic") or "")
            self.state.save(room_id, state)
        return {"ok": True}

    def _peer_responses(self, room_id: str) -> list[dict[str, Any]]:
        room = self.peer.store.get_room(room_id)
        return peer_responses(room)

    def _min_confidence(self, value: Any = None) -> float:
        if value is None:
            return self.settings.peer_agent_min_confidence
        return _clamp_float(value, default=self.settings.peer_agent_min_confidence)

    def _timeout(self, value: Any = None) -> float:
        if value is None:
            return self.settings.peer_agent_room_timeout_seconds
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return self.settings.peer_agent_room_timeout_seconds

    def _ensure_enabled(self) -> None:
        if not self.settings.peer_agent_enabled:
            raise RuntimeError("peer-agent is disabled by AMO_PEER_AGENT_ENABLED")
