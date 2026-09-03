"""Turn official directory state into the search terms founders actually type.

Batch labels are the one part of this product guaranteed to go stale. YC opens a
new batch every few months, and the previous one stops producing early signals the
moment it is fully published -- every post about it then describes a company that
is already listed. Hardcoding "YC S26" is exactly how a launch monitor quietly
starts hunting a batch it can no longer be early about.

So the search vocabulary is derived from whatever the official directories
currently report as filling, and nothing here needs editing when W27 opens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: YC's own abbreviation for each batch season. W/S are visible in the directory's
#: own copy; F is visible in live founder posts ("Orca Aerospace (YC F26)"); X is
#: YC's published spring code ("Announcing YC X25, the first ever spring batch").
SEASON_CODES = {"winter": "W", "spring": "X", "summer": "S", "fall": "F"}

#: Spring 2026 appears in live founder posts as *both* "YC X26" (Minicor) and
#: "YC P26" (Andustry, Klaimee) while the directory calls all three "Spring 2026".
#: Treating those as different batches produced false EARLY alerts for companies
#: that were already listed, so a season may carry several equivalent codes.
SEASON_ALIASES = {"spring": ("X", "P")}

_BATCH_LABEL = re.compile(r"^(winter|spring|summer|fall)\s+(\d{4})$", re.IGNORECASE)
_COHORT_LABEL = re.compile(r"^SR\s*0*(\d{1,3})$", re.IGNORECASE)

#: Announcement language that carries no batch token. Kept as its own query family
#: so a batch token *sharpens* the search without gating it -- plenty of founders
#: post "we got into YC" and never name the batch.
YC_CLAIM_PHRASES = (
    "accepted into Y Combinator",
    "we got into YC",
    "joining Y Combinator",
)
SPEEDRUN_CLAIM_PHRASES = (
    "accepted into a16z Speedrun",
    "joining a16z Speedrun",
)


def batch_codes(label: str | None) -> tuple[str, ...]:
    """Every code founders use for a batch label. ``"Spring 2026"`` -> X26, P26."""
    match = _BATCH_LABEL.match((label or "").strip())
    if not match:
        return ()
    season, year = match.groups()
    season = season.lower()
    letters = SEASON_ALIASES.get(season, (SEASON_CODES[season],))
    return tuple(f"{letter}{year[-2:]}" for letter in letters)


def batch_code(label: str | None) -> str | None:
    """The primary code for a batch label. ``"Fall 2026"`` -> ``"F26"``."""
    codes = batch_codes(label)
    return codes[0] if codes else None


def cohort_code(label: str | None) -> str | None:
    """``"SR007"`` or ``"sr7"`` -> ``"SR007"``."""
    match = _COHORT_LABEL.match((label or "").strip())
    return f"SR{int(match.group(1)):03d}" if match else None


def yc_batch_phrases(labels) -> list[str]:
    """Exact phrases founders use to name a YC batch, newest label first."""
    phrases: list[str] = []
    for label in labels or ():
        code = batch_code(label)
        if not code:
            continue
        for phrase in (f"YC {code}", f"Y Combinator {code}"):
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases


def speedrun_cohort_phrases(labels) -> list[str]:
    """Exact phrases founders use to name a Speedrun cohort, newest first."""
    phrases: list[str] = []
    for label in labels or ():
        code = cohort_code(label)
        if not code:
            continue
        for phrase in (f"Speedrun {code}", code):
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases


@dataclass(frozen=True)
class SocialTargets:
    """The batches worth hunting right now, as reported by the official directories."""

    yc_batches: tuple[str, ...] = ()
    speedrun_cohorts: tuple[str, ...] = ()

    @classmethod
    def from_db(cls, db) -> "SocialTargets":
        return cls(
            yc_batches=tuple(db.active_batches("yc_directory")),
            speedrun_cohorts=tuple(db.active_batches("speedrun")),
        )

    @property
    def is_empty(self) -> bool:
        """True when no official snapshot has told us what is currently filling."""
        return not yc_batch_phrases(self.yc_batches) and not speedrun_cohort_phrases(
            self.speedrun_cohorts
        )

    def describe(self) -> str:
        parts = yc_batch_phrases(self.yc_batches)[:1] + speedrun_cohort_phrases(
            self.speedrun_cohorts
        )[:1]
        return ", ".join(parts) or "none"


def _or_group(phrases) -> str:
    return " OR ".join(f'"{phrase}"' for phrase in phrases)


def x_queries(targets: SocialTargets) -> list[str]:
    """X recent-search queries routed through Serper Google Search.
    
    Using site:x.com instead of native X API to bypass billing limits.
    """
    queries: list[str] = []

    yc = yc_batch_phrases(targets.yc_batches)
    if yc:
        queries.append(f"site:x.com ({_or_group(yc)})")
    queries.append(f"site:x.com ({_or_group(YC_CLAIM_PHRASES)})")

    speedrun = speedrun_cohort_phrases(targets.speedrun_cohorts)
    if speedrun:
        queries.append(f"site:x.com ({_or_group(speedrun)})")
    queries.append(f"site:x.com ({_or_group(SPEEDRUN_CLAIM_PHRASES)})")

    return queries


def linkedin_queries(targets: SocialTargets) -> list[str]:
    """Indexed-search queries scoped to public LinkedIn posts and company pages.

    One query *per batch* rather than one covering all of them. The provider caps
    a free-tier response at ten results, so folding two batches into a single OR
    group halves the coverage of each -- and the older, fully-published batch wins
    the ranking, which is exactly the wrong half to keep.
    """
    queries: list[str] = []

    for label in targets.yc_batches:
        phrases = yc_batch_phrases([label])
        if not phrases:
            continue
        queries.append(f"site:linkedin.com/posts ({_or_group(phrases)})")
        # Company pages are only worth searching when a batch token keeps the
        # result set tight; without one they return recruiting boilerplate.
        queries.append(f"site:linkedin.com/company ({_or_group(phrases)})")

    queries.append(f"site:linkedin.com/posts ({_or_group(YC_CLAIM_PHRASES)})")

    for label in targets.speedrun_cohorts:
        phrases = speedrun_cohort_phrases([label])
        if phrases:
            queries.append(f"site:linkedin.com/posts ({_or_group(phrases)})")

    return queries
