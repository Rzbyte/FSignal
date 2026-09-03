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
    official_check: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
