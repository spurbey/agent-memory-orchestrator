from __future__ import annotations


def _packet_commit_sha(packet: dict[str, object]) -> str:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    return str(commit.get("short_sha") or "")


def _packet_full_sha(packet: dict[str, object]) -> str:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    return str(commit.get("full_sha") or commit.get("short_sha") or "")


def _packet_evidence_refs(packet: dict[str, object]) -> list[str]:
    refs: list[str] = []
    for key in ("problem_refs", "rationale_refs", "validation_refs"):
        for item in packet.get(key, []) if isinstance(packet.get(key), list) else []:
            if isinstance(item, dict) and item.get("ref"):
                refs.append(str(item["ref"]))
    return list(dict.fromkeys(refs))
