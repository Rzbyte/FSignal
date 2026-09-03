"""Application configuration.

All runtime secrets are read from environment variables. A repository-local `.env`
file is loaded automatically for local development; production deployments can
inject the same variables directly through the hosting platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env", override=False)


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Persistence / scheduler
    database_path: str = os.getenv("DATABASE_PATH", "data/ghost_radar.db")
    startup_scan: bool = _bool("STARTUP_SCAN", True)

    # HTTP
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25"))
    user_agent: str = os.getenv("USER_AGENT", "FSignal/1.1 (+launch-monitor)")

    # Official sources
    yc_directory_url: str = os.getenv(
        "YC_DIRECTORY_URL", "https://www.ycombinator.com/companies"
    )
    # The public directory is a client-side Algolia app. The launch-date replica
    # sorts newest-listed first, which is what a launch monitor actually needs;
    # the relevance index is kept only as a fallback if the replica is withdrawn.
    yc_index_name: str = os.getenv("YC_INDEX_NAME", "YCCompany_By_Launch_Date_production")
    yc_fallback_index_name: str = os.getenv("YC_FALLBACK_INDEX_NAME", "YCCompany_production")
    # How many currently-filling batches/cohorts to treat as "active" for social
    # targeting. Applies to both YC batches and Speedrun cohorts.
    active_batch_count: int = int(os.getenv("ACTIVE_BATCH_COUNT", "2"))
    # A full facet-sliced crawl reaches every company; between crawls a single
    # recent-window query is enough to catch new listings.
    yc_full_crawl_interval_minutes: float = float(
        os.getenv("YC_FULL_CRAWL_INTERVAL_MINUTES", "720")
    )
    # An EARLY verdict is only defensible against a reasonably fresh snapshot.
    snapshot_max_age_minutes: float = float(os.getenv("SNAPSHOT_MAX_AGE_MINUTES", "60"))
    # The public Speedrun directory is a Next.js app that calls this first-party
    # API itself, so this is the canonical data path rather than a mirror.
    speedrun_url: str = os.getenv(
        "SPEEDRUN_URL", "https://speedrun.a16z.com/companies"
    )
    speedrun_api_url: str = os.getenv(
        "SPEEDRUN_API_URL",
        "https://speedrun-api.a16z.com/api/companies/companies/?limit=500",
    )
    speedrun_fallback_url: str = os.getenv(
        "SPEEDRUN_FALLBACK_URL",
        "https://speedrun-talent-network.com/collections/speedrun-companies",
    )

    # Social sources
    x_bearer_token: str = os.getenv("X_BEARER_TOKEN", "")
    x_max_results: int = int(os.getenv("X_MAX_RESULTS", "50"))
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    # Google indexes public LinkedIn posts with a lag, so a one-day window misses
    # announcements that are still genuinely early. A week is the useful default:
    # company-level dedup means the wider window cannot produce repeat alerts.
    linkedin_lookback: str = os.getenv("LINKEDIN_LOOKBACK", "w")
    # The indexed fallback for X needs a wider window than LinkedIn. Narrowing it
    # to a week collapses the result set onto profile pages rather than posts,
    # because individual X status URLs are indexed with a longer lag.
    x_indexed_lookback: str = os.getenv("X_INDEXED_LOOKBACK", "m")
    min_signal_confidence: int = int(os.getenv("MIN_SIGNAL_CONFIDENCE", "60"))
    gtm_high_priority_threshold: int = int(os.getenv("GTM_HIGH_PRIORITY_THRESHOLD", "80"))

    # Per-source polling intervals (minutes). Each source runs independently.
    # Tune X_SCAN_INTERVAL_MINUTES to stay within your X API rate-limit budget.
    x_scan_interval_minutes: float = float(os.getenv("X_SCAN_INTERVAL_MINUTES", "10"))
    linkedin_scan_interval_minutes: float = float(os.getenv("LINKEDIN_SCAN_INTERVAL_MINUTES", "15"))
    yc_scan_interval_minutes: float = float(os.getenv("YC_SCAN_INTERVAL_MINUTES", "20"))
    speedrun_scan_interval_minutes: float = float(os.getenv("SPEEDRUN_SCAN_INTERVAL_MINUTES", "30"))
    ghost_recheck_interval_minutes: float = float(os.getenv("GHOST_RECHECK_INTERVAL_MINUTES", "10"))

    # Slack destination can be a channel ID (C...) or DM/conversation ID (D...).
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_channel_id: str = os.getenv("SLACK_CHANNEL_ID", "")

    # Pond Protocol V1
    pond_access_key: str = os.getenv("POND_ACCESS_KEY", "")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    # Deterministic local demo. Production must set this to false.
    demo_mode: bool = _bool("DEMO_MODE", False)


settings = Settings()
