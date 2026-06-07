from __future__ import annotations

from copy import deepcopy
from typing import Any

ATLAS_RESEARCH_GUIDANCE = {
    "domains": [
        "personal-performance",
        "creative-technical-operator",
        "entrepreneurship",
        "negotiation",
        "small-business-systems",
        "studio-media-operations",
        "creator-operations",
        "assistant-reliability",
        "personal-automation",
    ],
    "specialty": (
        "Research and remember material directly useful to Dallas/Atlas as a "
        "creative-technical father/operator: personal performance, entrepreneurship, "
        "negotiation, small-business systems, studio/media/creator operations, and "
        "Atlas assistant reliability or personal automation that improves daily life."
    ),
    "suggested_sources": [
        "operator/founder interviews with specific tactics and constraints",
        "negotiation and small-business operating-system case studies",
        "studio, media, and creator-operations breakdowns with numbers or workflows",
        "AI assistant reliability, evals, automation, and memory systems with implementation detail",
        "father/operator personal performance systems grounded in practice rather than generic motivation",
    ],
    "research_guidance": {
        "include": [
            "creative-technical father/operator personal performance",
            "entrepreneurship, negotiation, offers, sales systems, and small-business operations",
            "studio, media, creator, and content operations with reusable workflows",
            "Atlas/Neo/Hermes assistant reliability, memory quality, evals, and personal automation directly useful to Dallas",
            "specific mechanisms, numbers, tradeoffs, examples, and anti-patterns",
        ],
        "exclude_unless_requested": [
            "M365 or day-job IT administration",
            "broad AI hype, generic model news, and undifferentiated futurism",
            "beat-store tactics or generic music marketing",
            "generic productivity sludge, motivation, hustle content, and platitudes",
        ],
        "quality_bar": (
            "Prefer source-grounded, practitioner-specific knowledge with provenance, constraints, "
            "and recall cues. Reject filler, sponsor reads, vibes, and ungrounded advice."
        ),
    },
}

DEFAULT_AGENT_PROFILES: dict[str, dict[str, Any]] = {
    "atlas": ATLAS_RESEARCH_GUIDANCE,
    "arc": {
        "domains": ["software-leadership", "code-quality", "agent-systems", "reliability"],
        "specialty": "Software lead for Neo/Hermes/Atlas reliability, quality gates, tests, and shippable engineering changes.",
        "suggested_sources": ["official docs", "source repositories", "incident notes", "test results"],
        "research_guidance": {"quality_bar": "Prefer reproducible engineering evidence and implementation detail over commentary."},
    },
    "neon": {
        "domains": ["design", "interfaces", "visual-systems", "product-experience"],
        "specialty": "Product/design agent focused on interfaces, visual systems, and user experience for Dallas's tools.",
        "suggested_sources": ["design teardown", "product UX case study", "interface implementation notes"],
        "research_guidance": {"quality_bar": "Prefer concrete interaction patterns, visual references, and implementation constraints."},
    },
    "wave": {
        "domains": ["media", "creator-operations", "audio", "studio-systems"],
        "specialty": "Media and studio operations agent focused on creator workflows, audio/video systems, and production leverage.",
        "suggested_sources": ["studio workflow breakdown", "creator operations interview", "media business case study"],
        "research_guidance": {"quality_bar": "Prefer operational detail, economics, repeatable workflows, and source provenance."},
    },
}


def default_agent_profile(name: str) -> dict[str, Any]:
    """Return a copy of the source-controlled default profile for an agent name."""

    return deepcopy(DEFAULT_AGENT_PROFILES.get((name or "").strip().lower(), {}))


def merge_agent_defaults(name: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge source-controlled agent guidance without overwriting explicit values."""

    merged = dict(values or {})
    defaults = default_agent_profile(name)
    if not defaults:
        return merged

    for key in ("specialty", "skill_notes"):
        if defaults.get(key) and not merged.get(key):
            merged[key] = defaults[key]

    default_domains = list(defaults.get("domains") or [])
    if default_domains and not merged.get("domains"):
        merged["domains"] = default_domains

    config = dict(merged.get("config") or {})
    config.setdefault("source_default_agent_profile", True)
    config.setdefault("research_guidance", defaults.get("research_guidance") or {})
    config.setdefault("suggested_sources", list(defaults.get("suggested_sources") or []))
    merged["config"] = config
    return merged
