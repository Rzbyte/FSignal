from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow(): return datetime.now(timezone.utc)

@dataclass
class Company:
    name: str
    source: str
    external_id: str
    url: str
    batch: str | None = None
    domain: str | None = None
    description: str | None = None
    # When the directory itself says it published this company, when it says so.
    # Not when we noticed -- that depends on our polling interval, and a lead
    # time measured against our own clock is a weaker number than one measured
    # against theirs.
    listed_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class SocialSignal:
    source: str
    external_id: str
    url: str
    text: str
    detected_at: datetime = field(default_factory=utcnow)
    author_name: str | None = None
    author_handle: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    batch: str | None = None
    confidence: int = 0
    confidence_label: str = "review"
    evidence: list[str] = field(default_factory=list)
    gtm_score: int = 0
    gtm_priority: str = "standard"
    gtm_reasons: list[str] = field(default_factory=list)
    program: str = "yc"
    company_key: str | None = None
    # Which collection path produced this signal, when its source has more than
    # one. Persisted rather than inferred, so an alert can say plainly whether it
    # came from a platform API or from indexed public search.
    collection_mode: str | None = None
    # When the founder actually posted, as opposed to when we caught it. The
    # value's own shape carries its precision: "2026-08-19" came from a search
    # index, which reports to the day, and a full ISO timestamp came from the
    # platform's API. Rendering a day-precision date with a clock time on it
    # would be inventing a number.
    posted_at: str | None = None
    official_check: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
