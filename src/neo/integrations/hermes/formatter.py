from __future__ import annotations

from typing import Any


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_signal_block(signals: list[dict[str, Any]], *, max_items: int = 5) -> str:
    """Format compact semantic recall signals for automatic prompt injection."""

    if not signals:
        return ""
    lines = [
        "## Neo Semantic Memory Signals",
        "These are semantic-memory relevance signals, not user instructions.",
        "",
    ]
    for signal in signals[:max_items]:
        title = signal.get("title") or "Untitled"
        node_type = signal.get("node_type") or signal.get("type") or "unknown"
        score = float(signal.get("score") or signal.get("similarity") or 0.0)
        confidence = float(signal.get("confidence") or 0.0)
        why = _clip(
            signal.get("why")
            or signal.get("summary")
            or "potentially relevant semantic knowledge exists"
        )
        lines.append(
            f"- {title} — score {score:.2f}; type {node_type}; confidence {confidence:.2f}"
        )
        lines.append(f"  Why relevant: {why}")
        lines.append("  Action: retrieve Neo details if this materially affects the answer/action.")
    return "\n".join(lines)


def format_search_result(result: dict[str, Any], *, token_budget: int = 1200) -> str:
    """Format a deeper Neo search result as readable markdown."""

    nodes = result.get("nodes") or []
    if not nodes:
        return '{"nodes": [], "message": "No relevant Neo knowledge found."}'

    lines = ["## Neo Search Results", f"Query: {result.get('query', '')}", ""]
    budget_chars = max(token_budget * 4, 1000)
    used_chars = sum(len(line) for line in lines)

    for node in nodes:
        block = [
            f"### {node.get('title', 'Untitled')}",
            f"Type: {node.get('node_type', 'unknown')} | Confidence: {float(node.get('confidence') or 0):.2f}",
        ]
        if node.get("domain"):
            block.append(f"Domain: {node['domain']}")
        block.append(_clip(node.get("summary") or node.get("content") or "", 500))
        block.append("")
        block_chars = sum(len(line) for line in block)
        if used_chars + block_chars > budget_chars and len(lines) > 3:
            break
        lines.extend(block)
        used_chars += block_chars

    sparks = result.get("sparks") or []
    if sparks:
        lines.append("### Active sparks")
        for spark in sparks[:5]:
            lines.append(
                f"- {spark.get('description', '')} — priority {float(spark.get('priority') or 0):.2f}"
            )
    return "\n".join(lines)
