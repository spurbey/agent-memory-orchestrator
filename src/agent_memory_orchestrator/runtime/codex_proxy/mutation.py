from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult


def render_ranked_tool_hits(
    result: RankToolHitsResult,
    *,
    raw_ref: str,
    raw_output: str,
    max_hits: int = 5,
    max_lines_per_hit: int = 2,
    max_raw_excerpt_chars: int = 1800,
) -> str:
    """Render ranked rg/grep results for a proxy-mutated tool output.

    The proxy slice is ranked-first, not ranked-only: the model sees AMO's
    ranking plus a stable raw reference and bounded excerpt. Lossless raw
    recovery is owned by the raw store, not by this text block.
    """

    lines: list[str] = ["AMO_RANKED_TOOL_HITS"]
    if not result.ranked_hits:
        lines.append("No ranked hits were produced.")
    for index, hit in enumerate(result.ranked_hits[:max_hits], start=1):
        reasons = ", ".join(hit.reason_codes[:4])
        symbols = ", ".join(hit.symbol_node_ids[:3]) if hit.symbol_node_ids else "none"
        lines.append(f"{index}. {hit.path} score={hit.score:.4f} matches={hit.match_count}")
        lines.append(f"   reasons={reasons or 'none'}")
        lines.append(f"   mapped_symbols={symbols}")
        for line in hit.line_refs[:max_lines_per_hit]:
            lines.append(f"   line {line.line}: {line.text}")

    lines.extend(("", f"RAW_OUTPUT_REF {raw_ref}", "RAW_OUTPUT_EXCERPT"))
    excerpt = _bounded_excerpt(raw_output, max_chars=max_raw_excerpt_chars)
    lines.append(excerpt if excerpt else "<empty>")
    return "\n".join(lines)


def _bounded_excerpt(text: str, *, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "\n... <raw output truncated; use RAW_OUTPUT_REF for lossless recovery>"


__all__ = ["render_ranked_tool_hits"]
