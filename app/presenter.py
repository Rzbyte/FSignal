"""Presentation layer for Slack alerts.

Everything here is display-only. It reads facts the pipeline has already decided
and persisted, and turns them into something a Senior GTM professional can act on
in a few seconds. No detection, scoring, matching or state logic lives here.

Three jobs the raw persisted record cannot do on its own:

* **Founder identity.** X hands us an author; LinkedIn does not. The name is
  sitting in the post title ("Michael Wang's Post - LinkedIn") and in the profile
  slug, so it is recovered rather than shown as "See post".
* **Evidence phrasing.** The stored evidence is internal ("Company identity
  extracted: Lark (via program_tag)"). A user should never see a matcher's
  vocabulary, and weak items should not crowd out strong ones.
* **Verification link.** An unlisted company has no YC profile page, so inventing
  one would be a lie. The directory search filtered to its batch is real, and it
  is the exact view that proves the claim.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .targeting import SEASON_CODES

YC_DIRECTORY = "https://www.ycombinator.com/companies"
SPEEDRUN_DIRECTORY = "https://speedrun.a16z.com/companies"

#: Reverse of the season map, so "F26" can name the batch YC itself displays.
_CODE_TO_SEASON = {}
for _season, _letter in SEASON_CODES.items():
    _CODE_TO_SEASON.setdefault(_letter, _season.title())
_CODE_TO_SEASON.setdefault("P", "Spring")  # Spring 2026 also ships as P26.

_BATCH_CODE = re.compile(r"^([WXSFP])(\d{2})$", re.IGNORECASE)

#: Search-result furniture that is not part of what the founder wrote.
_CHROME_PREFIX = re.compile(
    r"^.{0,80}?['’]s\s+Post\s*[-–—|]?\s*LinkedIn\s*",
    re.IGNORECASE,
)
_CHROME_TAIL = re.compile(r"\s*[-–—|]\s*LinkedIn\s*$", re.IGNORECASE)
_POST_AUTHOR = re.compile(r"^\s*(.{2,60}?)['’]s\s+Post\b", re.IGNORECASE)
_PROFILE_SLUG = re.compile(r"linkedin\.com/posts/([A-Za-z0-9\-]+)_", re.IGNORECASE)

SOURCE_LABELS = {"linkedin": "LINKEDIN", "x": "X"}

#: Platform names as their owners write them. `"linkedin".title()` gives
#: "Linkedin", which reads as a typo in a product that is arguing for its own
#: credibility.
SOURCE_DISPLAY = {"linkedin": "LinkedIn", "x": "X"}

#: The scorer's reasons are written for the audit trail. These are the same facts
#: at the length a triage row can carry.
_GTM_REASON_DISPLAY = {
    "Founder is directly reachable from the post": "Founder directly reachable",
    "Current accelerator cohort identified": "Current cohort identified",
    "Company web identity is available": "Company website available",
    "Commercial/B2B language detected": "Commercial/B2B language",
    "Finance/operations relevance detected": "Finance/operations relevance",
}


def source_display(signal: dict) -> str:
    """The platform, and how we reached it when that is not the obvious way.

    A post found through public search carries less than one pulled from the
    platform's own API -- no bio, no profile URL, no exact post time. Saying so
    in the alert costs one parenthetical and keeps the reader from assuming
    metadata that is not there.
    """
    source = (signal.get("source") or "").lower()
    name = SOURCE_DISPLAY.get(source, source.title() or "Unknown")
    if signal.get("collection_mode") == "indexed_fallback":
        return f"{name} (indexed search)"
    return name


def display_reasons(signal: dict, limit: int = 3) -> list[str]:
    """Why this deserves attention now, in triage-length phrasing."""
    from .intelligence import compact_gtm_reasons

    return [
        _GTM_REASON_DISPLAY.get(reason, reason)
        for reason in compact_gtm_reasons(signal, limit)
    ]


# --------------------------------------------------------------------------- #
# Batch and programme naming                                                   #
# --------------------------------------------------------------------------- #


def program_label(signal: dict) -> str:
    return "SPEEDRUN" if (signal.get("program") or "yc").lower() == "speedrun" else "YC"


def official_batch_label(batch: str | None) -> str | None:
    """``"F26"`` -> ``"Fall 2026"``: the label the official directory displays."""
    match = _BATCH_CODE.match((batch or "").strip())
    if not match:
        return None
    letter, year = match.group(1).upper(), match.group(2)
    season = _CODE_TO_SEASON.get(letter)
    return f"{season} 20{year}" if season else None


def program_batch(signal: dict) -> str:
    """``"YC F26"`` -- how the state line and status name the cohort."""
    batch = signal.get("batch")
    return f"{program_label(signal)} {batch}" if batch else program_label(signal)


# --------------------------------------------------------------------------- #
# Identity                                                                     #
# --------------------------------------------------------------------------- #


def _titleise_slug(slug: str) -> str | None:
    tokens = [token for token in slug.split("-") if token]
    # Profile slugs carry a disambiguating suffix: michael-wang-40061923b.
    while tokens and (any(ch.isdigit() for ch in tokens[-1]) or len(tokens[-1]) <= 2):
        tokens.pop()
    if not tokens or len(tokens) > 4:
        return None
    return " ".join(token.capitalize() for token in tokens)


def founder_display(signal: dict) -> str:
    """The human to contact, or an honest admission that we do not know.

    Never "See post": a field whose value is an instruction is not a value.
    """
    name = (signal.get("author_name") or "").strip()
    handle = (signal.get("author_handle") or "").strip()
    if name and handle:
        return f"{name} (@{handle})"
    if name:
        return name
    if handle:
        return f"@{handle}"

    url = (signal.get("url") or "").lower()
    if "linkedin.com/company/" in url:
        return "Company page"

    raw = signal.get("raw_json")
    title = ""
    if raw:
        try:
            title = (json.loads(raw) if isinstance(raw, str) else raw).get("title", "")
        except (TypeError, json.JSONDecodeError):
            title = ""

    match = _POST_AUTHOR.match(title or "")
    if match:
        candidate = match.group(1).strip()
        company = (signal.get("company_name") or "").lower()
        # "Lantern AI (YC F26)'s Post" is the company posting, not a person.
        if company and company in candidate.lower():
            return "Company page"
        return candidate

    slug_match = _PROFILE_SLUG.search(signal.get("url") or "")
    if slug_match:
        titled = _titleise_slug(slug_match.group(1))
        if titled:
            return titled

    return "Unknown founder"


# --------------------------------------------------------------------------- #
# Outreach                                                                     #
# --------------------------------------------------------------------------- #


def founder_profile_url(signal: dict) -> str | None:
    """Where to actually reach the person, when the post says.

    The alert told a GTM reader who announced and when, and then stopped -- which
    is one step short of the job. The brief is written by someone who wants to
    contact these founders, so the alert should end where the outreach starts.
    """
    handle = (signal.get("author_handle") or "").strip().lstrip("@")
    if handle and (signal.get("source") or "").lower() == "x":
        return f"https://x.com/{handle}"

    url = signal.get("url") or ""
    if "linkedin.com/company/" in url.lower():
        # A company page has no person behind it to open.
        return None
    slug = _PROFILE_SLUG.search(url)
    # LinkedIn post URLs embed the author's own profile slug, so this is their
    # real profile rather than a guess.
    return f"https://www.linkedin.com/in/{slug.group(1)}" if slug else None


def company_site_url(signal: dict) -> str | None:
    """The company's own site, when a domain was actually resolved."""
    domain = (signal.get("company_domain") or "").strip()
    if not domain:
        return None
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


def outreach_links(signal: dict) -> str | None:
    """One line a reader can act on, or nothing rather than a dead label."""
    parts = []
    profile = founder_profile_url(signal)
    if profile:
        handle = (signal.get("author_handle") or "").strip().lstrip("@")
        label = f"@{handle}" if handle else "Founder profile"
        parts.append(f"<{profile}|{label}>")
    site = company_site_url(signal)
    if site:
        parts.append(f"<{site}|{(signal.get('company_domain') or '').strip()}>")
    return "Reach out · " + " · ".join(parts) if parts else None


def company_display(signal: dict) -> str:
    """``Lark`` or ``Lark · lark.ai`` when a domain was actually resolved."""
    company = signal.get("company_name") or signal.get("company_domain") or "Unknown company"
    domain = (signal.get("company_domain") or "").strip()
    return f"{company} · {domain}" if domain and signal.get("company_name") else company


def source_badge(signal: dict) -> str:
    source = (signal.get("source") or "").lower()
    label = SOURCE_LABELS.get(source, source.upper() or "UNKNOWN")
    if signal.get("collection_mode") == "indexed_fallback":
        label = f"{label} · INDEXED SEARCH"
    return f"`SOURCE · {label}`"


# --------------------------------------------------------------------------- #
# Excerpt                                                                      #
# --------------------------------------------------------------------------- #


def excerpt(signal: dict, limit: int = 260) -> str:
    """The shortest run of the founder's own words that carries the claim."""
    text = (signal.get("text") or "").strip()
    if not text:
        return ""
    text = _CHROME_PREFIX.sub("", text)
    text = _CHROME_TAIL.sub("", text).strip()

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    company = (signal.get("company_name") or "").lower()
    batch = (signal.get("batch") or "").lower()

    # Prefer the sentences that actually name the company or the batch; a
    # trailing "We're building AI for wholesale ..." is search-result filler.
    strong = [
        sentence
        for sentence in sentences
        if (company and company in sentence.lower()) or (batch and batch in sentence.lower())
    ]
    chosen, total = [], 0
    for sentence in strong or sentences:
        if chosen and total + len(sentence) > limit:
            break
        chosen.append(sentence)
        total += len(sentence)
        if len(chosen) == 2:
            break

    picked = " ".join(chosen) if chosen else text[:limit]
    picked = re.sub(r"\s*\.{3,}\s*$", "...", picked.strip())
    return picked[:limit].strip()


# --------------------------------------------------------------------------- #
# Evidence                                                                     #
# --------------------------------------------------------------------------- #

#: (rank, matcher, renderer). Lower rank shows first. Anything not listed is
#: dropped -- including the raw claim string and the directory-match line, which
#: the OFFICIAL CHECK block states far more clearly on its own.
_EVIDENCE_RULES: tuple[tuple[int, re.Pattern, str], ...] = (
    (1, re.compile(r"^Batch/cohort identified:", re.IGNORECASE), "batch"),
    (2, re.compile(r"^Company identity extracted:", re.IGNORECASE), "company"),
    (3, re.compile(r"first person", re.IGNORECASE), "First-person founder announcement"),
    (3, re.compile(r"company as the subject", re.IGNORECASE), "Company announced its own acceptance"),
    (3, re.compile(r"own LinkedIn page", re.IGNORECASE), "Posted from the company's own page"),
    (4, re.compile(r"^Company domain resolved:\s*(.+)$", re.IGNORECASE), "domain"),
    (5, re.compile(r"^Public author identity available:\s*(.+)$", re.IGNORECASE), "author"),
)


def display_evidence(signal: dict, limit: int = 3) -> list[str]:
    """Strongest evidence first, in the user's language rather than the matcher's."""
    raw = signal.get("evidence_json") or "[]"
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        stored = []

    items: list[tuple[int, str]] = []
    for entry in stored:
        entry = str(entry)
        for rank, pattern, template in _EVIDENCE_RULES:
            match = pattern.search(entry)
            if not match:
                continue
            if template == "batch":
                quoted = _quoted_program_phrase(signal)
                items.append((rank, f'Explicit "{quoted}" statement'))
            elif template == "company":
                items.append((rank, f"Company identity: {signal.get('company_name')}"))
            elif template == "domain":
                items.append((rank, f"Verified company domain: {match.group(1).strip()}"))
            elif template == "author":
                items.append((rank, f"Founder identity available: {match.group(1).strip()}"))
            else:
                items.append((rank, template))
            break

    seen, ordered = set(), []
    for _, text in sorted(items, key=lambda item: item[0]):
        if text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered[:limit]


def _quoted_program_phrase(signal: dict) -> str:
    """Quote the batch phrase as the founder actually wrote it, when present."""
    text = signal.get("text") or ""
    batch = signal.get("batch") or ""
    if batch:
        for candidate in (
            rf"Y Combinator\s+{re.escape(batch)}",
            rf"YC\s+{re.escape(batch)}",
            rf"a16z Speedrun\s+{re.escape(batch)}",
        ):
            found = re.search(candidate, text, re.IGNORECASE)
            if found:
                return found.group(0)
    return program_batch(signal)


# --------------------------------------------------------------------------- #
# Verification link                                                            #
# --------------------------------------------------------------------------- #


def verify_url(signal: dict) -> str:
    """Where a reader goes to check the claim for themselves.

    An unlisted company has no profile page, so this is the official directory
    search narrowed to its batch -- a real URL showing the real absence. Never a
    fabricated company profile.
    """
    if program_label(signal) == "SPEEDRUN":
        return SPEEDRUN_DIRECTORY

    from urllib.parse import urlencode

    params = {}
    if signal.get("company_name"):
        params["query"] = signal["company_name"]
    label = official_batch_label(signal.get("batch"))
    if label:
        params["batch"] = label
    return f"{YC_DIRECTORY}?{urlencode(params)}" if params else YC_DIRECTORY


# --------------------------------------------------------------------------- #
# Official check                                                               #
# --------------------------------------------------------------------------- #


def official_check(signal: dict) -> dict:
    try:
        return json.loads(signal.get("official_check_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _utc_clock(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"{moment.astimezone(timezone.utc):%H:%M} UTC"


def official_check_lines(signal: dict) -> tuple[str, str, str | None]:
    """(headline, provenance, optional detail) for the credibility block."""
    check = official_check(signal)
    if not check:
        return ("Official check not recorded for this signal", "", None)

    directory = "Speedrun" if program_label(signal) == "SPEEDRUN" else "YC"
    batch_label = official_batch_label(signal.get("batch"))
    scope = f"{directory} {batch_label}" if batch_label else f"the {directory} directory"

    if check.get("matched"):
        headline = f"Listed in {scope} as *{check.get('official_name')}*"
    else:
        headline = f"*Not found in {scope}*"

    size = check.get("snapshot_size") or check.get("records_checked") or 0
    provenance = f"{size:,} {directory} records checked · snapshot {_utc_clock(check.get('snapshot_taken_at'))}"

    tried = check.get("methods_tried") or []
    detail = None
    if not check.get("matched") and tried:
        readable = []
        if "domain" in tried:
            readable.append("website")
        if "exact_name" in tried:
            readable.append("exact name")
        if "batch_prefix" in tried:
            readable.append("shortened name within the batch")
        if readable:
            detail = "No match on " + ", ".join(readable)
    return (headline, provenance, detail)
