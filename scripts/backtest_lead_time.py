"""How much earlier than the directory does this actually find companies?

The live monitor answers that only after a company it flagged is later listed,
which takes as long as it takes. This answers it now, from data that is already
public, by comparing two timestamps that both belong to somebody else:

  * when the founder posted, per the search index; and
  * when YC published the company, per YC's own `launched_at`.

Neither is our clock, so the figure cannot be flattered by how often we poll.

**This is a backtest, not a record of alerts.** Nothing here was delivered to
Slack. It measures what the monitor would have caught had it been running when
these posts were indexed, and it is labelled that way everywhere it is reported.
Read it as "this is the lead the approach buys", never as "this is what we sent".

Two honest limits, both stated in the output:

  * the index reports a post's date to the day, so a lead is ±1 day;
  * only companies that YC has *since* listed can be measured at all. A company
    still unlisted has a lead of "at least N days, still counting", and those are
    reported separately rather than folded into the average.

    python scripts/backtest_lead_time.py
    python scripts/backtest_lead_time.py --window y   # d, w, m, y
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import Database, normalize_name
from app.extract import enrich_signal
from app.matcher import resolve_official
from app.models import SocialSignal
from app.sources.official import SpeedrunSource, YCDirectorySource
from app.sources.social import _X_STATUS_URL
from app.targeting import (
    SocialTargets,
    linkedin_queries,
    x_indexed_queries,
)

OUT = ROOT / "evidence" / "raw"

#: Only a *post* has a publication date worth measuring. A LinkedIn company
#: page is a real discovery signal -- the brief asks for those too -- but the
#: date a search index reports for one is when the page was created or last
#: crawled, not when anybody announced anything. Measuring lead from it inflated
#: the headline figure to 200 days on the first run, off a company page.
_DATED_POST = re.compile(
    r"^https?://(?:[a-z]+\.)?(?:x\.com/[A-Za-z0-9_]{1,15}/status/\d+"
    r"|linkedin\.com/posts/)",
    re.IGNORECASE,
)

#: "Aug 19, 2026" is what the index returns for a dated result.
_ABSOLUTE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})$")
#: "3 days ago", "2 weeks ago" -- resolved against the run time.
_RELATIVE = re.compile(r"^(\d+)\s+(hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
_RELATIVE_DAYS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30.44, "year": 365.25}


def parse_post_date(value: str | None, now: datetime) -> datetime | None:
    """The day a post was published, as the search index reports it."""
    text = (value or "").strip()
    match = _ABSOLUTE.match(text)
    if match:
        try:
            return datetime.strptime(text, "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    match = _RELATIVE.match(text)
    if match:
        days = int(match.group(1)) * _RELATIVE_DAYS[match.group(2).lower()]
        return now - timedelta(days=days)
    return None


async def search(client: httpx.AsyncClient, query: str, window: str) -> list[dict]:
    response = await client.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": 10, "tbs": f"qdr:{window}"},
        headers={"X-API-KEY": settings.serper_api_key,
                 "Content-Type": "application/json"},
    )
    if response.status_code >= 400:
        print(f"  ! {query[:60]}: HTTP {response.status_code}")
        return []
    return response.json().get("organic", [])


def signal_from(result: dict, source: str) -> SocialSignal | None:
    """Build a candidate exactly the way the production adapters do."""
    link = result.get("link", "") or ""
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""

    if source == "x":
        match = _X_STATUS_URL.match(link)
        if not match:
            return None
        external_id, url = match.group(2), link
    else:
        if "linkedin.com/" not in link:
            return None
        external_id, url = link, link

    signal = SocialSignal(
        source=source,
        external_id=external_id,
        url=url,
        text=" ".join(part for part in [snippet or title, title] if part).strip(),
    )
    enrich_signal(signal)
    return signal


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", default="y", choices=["d", "w", "m", "y"],
                        help="How far back to search (default: y)")
    parser.add_argument(
        "--batches",
        help="Comma-separated batch labels to backtest, e.g. "
             "'Fall 2026,Summer 2026,Spring 2026'. Defaults to whatever the live "
             "monitor is currently hunting. A lead can only be measured for a "
             "company YC has already listed, so older batches carry more of them.",
    )
    parser.add_argument(
        "--replay",
        help="Re-analyse the raw searches saved in a previous run's JSON instead "
             "of querying again. Makes the figures re-derivable with no API key.",
    )
    args = parser.parse_args()

    replayed = None
    if args.replay:
        replayed = json.loads(Path(args.replay).read_text())["raw_searches"]
    elif not settings.serper_api_key:
        print("SERPER_API_KEY is not set. Pass --replay to re-analyse a saved run.")
        return 1

    now = datetime.now(timezone.utc)
    print("Lead-time backtest — NOT a record of delivered alerts\n")

    # The official corpus, and the batches the monitor would currently hunt.
    yc, speedrun = await YCDirectorySource().collect(), await SpeedrunSource().collect()
    rows = [
        {
            "id": index,
            "name": company.name,
            "normalized_name": normalize_name(company.name),
            "batch": company.batch,
            "domain": company.domain,
            "url": company.url,
            "listed_at": company.listed_at,
        }
        for index, company in enumerate(yc + speedrun)
    ]
    database = Database(settings.database_path)
    if args.batches:
        targets = SocialTargets(
            yc_batches=tuple(
                label.strip() for label in args.batches.split(",") if label.strip()
            ),
            speedrun_cohorts=(),
        )
    else:
        targets = SocialTargets.from_db(database)
    if targets.is_empty:
        targets = SocialTargets(
            yc_batches=tuple(
                dict.fromkeys(c.batch for c in yc if c.batch)
            )[:2],
            speedrun_cohorts=tuple(
                dict.fromkeys(c.batch for c in speedrun if c.batch)
            )[:1],
        )
    print(f"Official corpus : {len(rows):,} companies "
          f"({sum(1 for r in rows if r['listed_at']):,} with a published listing time)")
    print(f"Batches hunted  : {targets.describe()}")
    print(f"Search window   : past {args.window}\n")

    # The same queries the live monitor issues. A backtest on different queries
    # would measure a different product.
    plan = [("x", q) for q in x_indexed_queries(targets)]
    plan += [("linkedin", q) for q in linkedin_queries(targets)]

    measured: dict[str, dict] = {}
    undated = unmatched = pages = 0
    raw: list[dict] = []

    if replayed is not None:
        plan = [(entry["source"], entry["query"]) for entry in replayed]
        saved = {(e["source"], e["query"]): e["results"] for e in replayed}

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        for source, query in plan:
            results = (
                saved[(source, query)]
                if replayed is not None
                else await search(client, query, args.window)
            )
            raw.append({"source": source, "query": query, "results": results})
            print(f"  {source:9s} {len(results):2d} results  {query[:62]}")

            for result in results:
                signal = signal_from(result, source)
                if signal is None or not signal.extraction.is_usable:
                    continue
                if not _DATED_POST.match(signal.url):
                    # A company page, which has no announcement date to measure.
                    pages += 1
                    continue
                posted = parse_post_date(result.get("date"), now)
                if posted is None:
                    undated += 1
                    continue

                check = resolve_official(
                    signal.company_name, signal.company_domain, rows, signal.batch
                )
                if check.match is None:
                    unmatched += 1
                    continue

                company = check.match
                listed_at = company.get("listed_at")
                lead_days = (
                    (datetime.fromisoformat(listed_at) - posted).total_seconds() / 86400
                    if listed_at
                    else None
                )
                key = company["name"]
                held = measured.get(key)
                # The earliest post about a company is the one that would have
                # triggered the alert, so it is the one that sets the lead.
                if held is None or posted < held["posted"]:
                    measured[key] = {
                        "company": key,
                        "batch": company.get("batch"),
                        "posted": posted,
                        "listed_at": listed_at,
                        "lead_days": lead_days,
                        "post_url": signal.url,
                        "source": source,
                        "matched_by": check.method,
                    }

    ahead = [m for m in measured.values()
             if m["lead_days"] is not None and m["lead_days"] > 0]
    behind = [m for m in measured.values()
              if m["lead_days"] is not None and m["lead_days"] <= 0]
    unlisted = [m for m in measured.values() if m["lead_days"] is None]

    print(f"\n{'-' * 72}")
    print(f"Companies resolved to the directory : {len(measured)}")
    print(f"  founder posted BEFORE YC listed   : {len(ahead)}")
    print(f"  founder posted after YC listed    : {len(behind)}")
    print(f"  no published listing time         : {len(unlisted)}")
    print(f"Skipped: {pages} company pages (no announcement date), "
          f"{undated} undated posts, {unmatched} with no directory match")

    if ahead:
        leads = sorted(m["lead_days"] for m in ahead)
        measurable = len(ahead) + len(behind)
        print(f"\nLead over the directory, in days ({len(leads)} companies):")
        print(f"  median {statistics.median(leads):.1f}   "
              f"mean {statistics.fmean(leads):.1f}   "
              f"min {leads[0]:.1f}   max {leads[-1]:.1f}")
        # The median alone hides the shape. Most founders announce the same day
        # the directory publishes; the value is in the tail that does not, and a
        # buyer deciding whether this is worth running needs to see both.
        week = sum(1 for lead in leads if lead >= 7)
        fortnight = sum(1 for lead in leads if lead >= 14)
        print(f"  {len(ahead)} of {measurable} measurable companies were announced "
              f"publicly before the directory listed them")
        print(f"  {week} of those by a week or more, {fortnight} by two weeks or more")
        print("\n  company                    batch    posted      listed      lead")
        for m in sorted(ahead, key=lambda m: -m["lead_days"]):
            print(f"  {m['company'][:26]:26s} {str(m['batch'])[:8]:8s} "
                  f"{m['posted'].date()}  {m['listed_at'][:10]}  "
                  f"{m['lead_days']:5.1f}d  [{m['source']}]")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "lead-time-backtest.json"
    path.write_text(json.dumps({
        "captured_at": now.isoformat(),
        "what_this_is": (
            "A BACKTEST. None of these was delivered as a Slack alert. It compares "
            "the founder post date reported by the search index against YC's own "
            "published launched_at, using the same queries, extraction and matching "
            "the live monitor uses."
        ),
        "limits": [
            "The search index reports a post's date to the day, so each lead is +/-1 day.",
            "Only companies YC has since listed can be measured; still-unlisted ones "
            "are reported separately, not averaged in.",
            "Search ranking, not exhaustive retrieval: this is a sample of the posts "
            "that existed, never all of them.",
        ],
        "window": args.window,
        "batches_backtested": (
            args.batches or "the batches the live monitor is currently hunting"
        ),
        "official_corpus": len(rows),
        "batches": targets.describe(),
        "summary": {
            "resolved": len(measured),
            "posted_before_listing": len(ahead),
            "posted_after_listing": len(behind),
            "no_published_listing_time": len(unlisted),
            "median_lead_days": (
                round(statistics.median([m["lead_days"] for m in ahead]), 1)
                if ahead else None
            ),
            "mean_lead_days": (
                round(statistics.fmean([m["lead_days"] for m in ahead]), 1)
                if ahead else None
            ),
            "max_lead_days": (
                round(max(m["lead_days"] for m in ahead), 1) if ahead else None
            ),
            # The median hides the shape: most founders announce the same day the
            # directory publishes, and the value is in the tail that does not.
            "ahead_by_a_week_or_more": sum(
                1 for m in ahead if m["lead_days"] >= 7
            ),
            "ahead_by_two_weeks_or_more": sum(
                1 for m in ahead if m["lead_days"] >= 14
            ),
        },
        "companies": [
            {**m, "posted": m["posted"].isoformat(),
             "lead_days": round(m["lead_days"], 2) if m["lead_days"] is not None else None}
            for m in sorted(measured.values(),
                            key=lambda m: -(m["lead_days"] or 0))
        ],
        "raw_searches": raw,
    }, indent=2) + "\n")
    print(f"\nWritten to {path.relative_to(ROOT)}")
    print("Report this as a backtest. It is not evidence of delivered alerts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
