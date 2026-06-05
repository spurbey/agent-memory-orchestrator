from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...peer.agent import PeerAgentService
from ...peer.netd_runtime import PeerNetdRuntime
from ...peer.service import PeerService


def antelligent_status(settings: Settings) -> dict[str, Any]:
    peer_service = PeerService(settings)
    peer_status = _safe_call(peer_service.status)
    netd_status = _safe_call(lambda: PeerNetdRuntime(settings).status())
    llm_status = _llm_status(settings)
    worker_status = _worker_status(settings, peer_status=peer_status, netd_status=netd_status)
    return {
        "ok": True,
        "service": "antelligent",
        "daemon": {
            "ok": True,
            "host": settings.mcp_host,
            "port": settings.mcp_port,
            "local_only": settings.local_only,
        },
        "peer": peer_status,
        "netd": netd_status,
        "worker": worker_status,
        "llm": llm_status,
    }


def _llm_status(settings: Settings) -> dict[str, Any]:
    try:
        gateway = PeerAgentService(settings).llm
        local_ready = gateway.local_ollama_ready()
        provider_ready = gateway.provider_configured()
    except Exception as exc:
        return {
            "ok": False,
            "local_ollama_ready": False,
            "provider_configured": False,
            "error": str(exc),
        }
    return {
        "ok": True,
        "runtime": settings.peer_agent_runtime,
        "model": settings.peer_agent_model or settings.qwen_model,
        "local_ollama_ready": local_ready,
        "provider_configured": provider_ready,
        "initiator_api_fallback": settings.peer_agent_allow_initiator_api_fallback,
        "retrieval_only_fallback": settings.peer_agent_allow_retrieval_only_responses,
    }


def _worker_status(settings: Settings, *, peer_status: dict[str, Any], netd_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(settings.peer_agent_enabled),
        "enabled": settings.peer_agent_enabled,
        "normal_worker": "peer-agent watch",
        "netd_api_ok": bool(netd_status.get("api_ok")),
        "room_count": int(peer_status.get("room_count") or 0) if peer_status.get("ok") else 0,
        "max_peers": settings.peer_agent_max_peers,
        "room_timeout_seconds": settings.peer_agent_room_timeout_seconds,
    }


def _safe_call(fn: Any) -> dict[str, Any]:
    try:
        payload = fn()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "non_object_status"}


__all__ = ["antelligent_status"]
