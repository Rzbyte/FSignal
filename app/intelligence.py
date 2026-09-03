"""Explainable scoring for launch signals.

There is exactly one confidence scale here. An earlier version scored the same
post twice -- once during extraction, once during assessment -- and merged the
two with `max()`, which produced a number on no scale at all and let a 56 from
one system masquerade as a 56 from the other.

Confidence answers "how strong is the evidence this is a genuine accelerator
announcement about this company?" GTM priority answers a different question,
"how actionable is this discovery for outbound right now?", so the two stay
separate. Both are deterministic and both persist the evidence behind them, so a
reviewer can see why a number is what it is instead of trusting a model.
"""

from __future__ import annotations

import json

from .config import settings

#: Evidence weights. A parenthesised program tag ("Locke (YC S26)") is the single
#: strongest identity signal founders produce, which is why it outweighs the
#: looser prose patterns.
IDENTITY_WEIGHTS = {"program_tag": 35, "claim_anchor": 25, "possessive": 25}

#: How the acceptance is stated. A founder writing "we got into YC" is the
#: strongest form, but a company-subject statement ("EVO HQ (YC F26) got into Y
#: Combinator!!") is nearly as good and must not score as if no claim existed --
#: that phrasing is exactly what genuine pre-listing announcements look like.
CLAIM_WEIGHTS = {"first_person": 25, "company_voice": 20}

#: A company's own page asserting "(YC F26)" is meaningful even with no claim.
VOICE_WEIGHTS = {"company_account": 12}

COMMERCIAL_TERMS = (
    "b2b", "enterprise", "saas", "platform", "api", "infrastructure",
    "fintech", "payments", "banking", "treasury", "finance", "accounting",
    "payroll", "commerce", "procurement", "operations", "logistics",
)
FINANCE_TERMS = (
    "fintech", "payments", "banking", "treasury", "finance", "accounting",
    "payroll", "card", "expense", "invoice", "billing",
)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    padded = f" {text.lower()} "
    return any(term in padded for term in terms)


def assess_signal(signal, official_match: dict | None = None):
    """Score a signal from the evidence its extraction actually found."""
    extraction = getattr(signal, "extraction", None)
    text = signal.text or ""
    evidence: list[str] = list(getattr(extraction, "evidence", []) or [])
    score = 0

    identity_source = getattr(extraction, "identity_source", None)
    score += IDENTITY_WEIGHTS.get(identity_source, 0)

    voice = getattr(extraction, "author_voice", "third_party")
    claim_kind = getattr(extraction, "claim_kind", None)
    score += CLAIM_WEIGHTS.get(claim_kind, 0) + VOICE_WEIGHTS.get(voice, 0)

    if claim_kind == "first_person":
        evidence.append("Founder is speaking in the first person about their own company")
    elif claim_kind == "company_voice":
        evidence.append("Acceptance stated with the company as the subject")
    if voice == "company_account":
        evidence.append("Posted from the company's own LinkedIn page")

    if signal.batch:
        score += 15
    if signal.company_domain:
        score += 10
        evidence.append(f"Company domain resolved: {signal.company_domain}")
    if signal.author_handle or signal.author_name:
        score += 5
        evidence.append(
            f"Public author identity available: {signal.author_handle or signal.author_name}"
        )

    if official_match is None:
        evidence.append("No matching official-directory entry at detection time")
    else:
        evidence.append(
            f"Already listed in the official directory as "
            f"{official_match.get('name')} ({official_match.get('batch')})"
        )

    signal.confidence = max(0, min(100, score))
    signal.confidence_label = (
        "high" if signal.confidence >= 80
        else "likely" if signal.confidence >= 65
        else "review"
    )
    signal.evidence = evidence

    # GTM priority rewards immediacy, reachability, and commercial fit. It does
    # not claim a company is a good customer; it ranks what deserves a human
    # first.
    gtm = 45 if official_match is None else 20
    reasons: list[str] = []
    if official_match is None:
        reasons.append("Pre-directory timing advantage")
    if signal.author_handle or signal.author_name or claim_kind == "first_person":
        gtm += 10
        reasons.append("Founder is directly reachable from the post")
    if signal.company_domain:
        gtm += 10
        reasons.append("Company web identity is available")
    if signal.batch:
        gtm += 10
        reasons.append("Current accelerator cohort identified")
    if _has_any(text, COMMERCIAL_TERMS):
        gtm += 10
        reasons.append("Commercial/B2B language detected")
    if _has_any(text, FINANCE_TERMS):
        gtm += 10
        reasons.append("Finance/operations relevance detected")

    signal.gtm_score = min(100, gtm)
    signal.gtm_priority = (
        "high" if signal.gtm_score >= settings.gtm_high_priority_threshold
        else "medium" if signal.gtm_score >= 60
        else "standard"
    )
    signal.gtm_reasons = reasons
    return signal


def _load_list(signal: dict, key: str, limit: int) -> list[str]:
    raw = signal.get(key) or "[]"
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        values = []
    return [str(item) for item in values[:limit]]


def compact_evidence(signal: dict, limit: int = 5) -> list[str]:
    """Return normalized evidence from a persisted signal row."""
    return _load_list(signal, "evidence_json", limit)


def compact_gtm_reasons(signal: dict, limit: int = 3) -> list[str]:
    return _load_list(signal, "gtm_reasons_json", limit)
