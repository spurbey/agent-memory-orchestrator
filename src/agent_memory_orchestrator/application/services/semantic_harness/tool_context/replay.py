from __future__ import annotations

import json
from pathlib import Path

from .extract import classify_tool_kind
from .models import CapturedToolResult
from .models import ShadowReplayReport
from .models import ToolOverlayEvalRecord
from .models import ToolOverlayJudgment
from .models import captured_tool_result_from_event
from .planner import ToolContextPlanner


class ShadowToolReplayService:
    """Runs shadow-only harness overlay decisions over captured PostToolUse rows."""

    def __init__(self, *, planner: ToolContextPlanner) -> None:
        self._planner = planner

    def replay_file(self, path: str | Path, *, repo_id: str, limit: int = 0) -> ShadowReplayReport:
        source_path = Path(path).expanduser().resolve()
        captured = tuple(_read_captured_tool_results(source_path, limit=limit))
        records: list[ToolOverlayEvalRecord] = []
        seen_card_ids: set[str] = set()
        for index, item in enumerate(captured):
            decision = self._planner.plan(
                repo_id=repo_id,
                captured=item,
                already_seen_card_ids=tuple(sorted(seen_card_ids)),
            )
            if decision.would_attach:
                for card in decision.harness_response.get("cards", []):
                    if card_id := card.get("card_id"):
                        seen_card_ids.add(str(card_id))
            records.append(
                ToolOverlayEvalRecord(
                    decision=decision,
                    judgment=_automatic_judgment(decision_targets(decision.harness_response), captured[index + 1 :]),
                )
            )
        return ShadowReplayReport(repo_id=repo_id, source_path=str(source_path), records=tuple(records))


def _read_captured_tool_results(path: Path, *, limit: int) -> list[CapturedToolResult]:
    captured: list[CapturedToolResult] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit and len(captured) >= limit:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            item = captured_tool_result_from_event(event)
            if item is not None:
                captured.append(item)
    return captured


def _automatic_judgment(targets: tuple[str, ...], future: tuple[CapturedToolResult, ...]) -> ToolOverlayJudgment:
    if not targets:
        return ToolOverlayJudgment(reason="no_card_targets")
    first_three = future[:3]
    first_five = future[:5]
    useful = _future_mentions_target(targets, first_three)
    if useful:
        return ToolOverlayJudgment(auto_useful=True, auto_mislead_candidate=False, reason="target_reused_within_next_3_tools")
    repeated_target_focus = sum(1 for item in first_five if _future_mentions_target(targets, (item,))) >= 5
    no_related_progress = not any(_is_edit_test_or_commit(item) for item in first_five)
    if repeated_target_focus and no_related_progress:
        return ToolOverlayJudgment(auto_useful=False, auto_mislead_candidate=True, reason="target_focus_without_edit_test_or_commit")
    return ToolOverlayJudgment(reason="manual_review_required")


def decision_targets(response: dict[str, object]) -> tuple[str, ...]:
    targets: list[str] = []
    cards = response.get("cards")
    if not isinstance(cards, list):
        return ()
    for card in cards:
        if not isinstance(card, dict):
            continue
        if next_action := card.get("next_action"):
            targets.extend(_target_fragments(str(next_action)))
        evidence = card.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict):
                    if path := item.get("path"):
                        targets.extend(_target_fragments(str(path)))
                    if node_id := item.get("node_id"):
                        targets.extend(_target_fragments(str(node_id)))
    return tuple(dict.fromkeys(target for target in targets if target))


def _target_fragments(value: str) -> list[str]:
    normalized = value.replace("\\", "/").strip()
    fragments = [normalized]
    if " " in normalized:
        fragments.extend(part.strip(".,:;()[]{}") for part in normalized.split())
    if "/" in normalized:
        fragments.append(normalized.rsplit("/", 1)[-1])
    return [fragment for fragment in fragments if len(fragment) >= 4]


def _future_mentions_target(targets: tuple[str, ...], items: tuple[CapturedToolResult, ...]) -> bool:
    lowered_targets = tuple(target.lower() for target in targets)
    for item in items:
        haystack = f"{item.command}\n{item.tool_response[:2000]}".replace("\\", "/").lower()
        if any(target in haystack for target in lowered_targets):
            return True
    return False


def _is_edit_test_or_commit(item: CapturedToolResult) -> bool:
    tool_kind = classify_tool_kind(item)
    command = item.command.lower()
    return tool_kind in {"apply_patch", "test_output"} or command.startswith("git commit") or "git commit" in command


__all__ = ["ShadowToolReplayService", "decision_targets"]
