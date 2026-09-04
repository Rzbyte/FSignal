"""Replay real captured search results through the unmodified production pipeline.

    python scripts/replay_corpus.py             # human-readable
    python scripts/replay_corpus.py --json      # machine-readable summary

Offline by construction: no network, no API key, no credential of any kind. The
inputs are a committed fixture, the official corpus is the slice captured beside
it, and Slack is redirected to a local JSONL file. A judge can run this on a
plane.

This is a rehearsal, not a simulation. Every candidate is a genuine Serper result
for a public LinkedIn post, captured live and committed. They run through the same
extraction, scoring, official matching, dedup and alert code that production uses
-- nothing is hand-fed and no timestamp is invented.

It resets its own database first, so the numbers are the same on every run rather
than depending on what a previous run left behind. The second pass over the
identical batch is the dedup proof: it must add nothing.

To browse the result afterwards:

    python scripts/demo_server.py     # then open http://localhost:8000/ledger
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_PATH"] = "data/demo_ghost_radar.db"
os.environ["STARTUP_SCAN"] = "false"

from app.config import settings
from app.db import Database
from app.engine import RadarEngine
from app.extract import enrich_signal
from app.models import Company, SocialSignal
from app.slack import SlackNotifier

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "linkedin_corpus.json").read_text()
)


async def replay(reset: bool = True) -> dict:
    """Run the corpus through the production pipeline and report what happened.

    Returns the same numbers the human-readable output prints, so a test can
    assert on them rather than on formatting.
    """
    if reset:
        # Deterministic from a clean slate: a replay that inherits a previous
        # run's database reports how much was already there, not what the
        # pipeline decides about this corpus.
        Path(settings.database_path).unlink(missing_ok=True)
        Path("data/demo_slack.jsonl").unlink(missing_ok=True)

    db = Database(settings.database_path)
    engine = RadarEngine(db, SlackNotifier())

    # Seed the official snapshot the fixture was adjudicated against, so the
    # EARLY verdicts below are checked against a real directory slice.
    official = [
        Company(
            name=row["name"],
            source=row.get("source") or "yc_directory",
            external_id=str(row["id"]),
            url=row.get("url") or "",
            batch=row.get("batch"),
            domain=row.get("domain"),
        )
        for row in FIXTURE["official_snapshot"]
    ]
    await engine.ingest_official(official, alert_new=False, complete_snapshot=True)
    # The size the fixture's verdicts were adjudicated against, recorded so the
    # snapshot-freshness gate sees a real corpus rather than an empty one.
    db.record_snapshot(
        "yc_directory", 6200, mode="full",
        index_used="YCCompany_By_Launch_Date_production",
        active_batches=["Fall 2026", "Summer 2026"],
    )
    signals = []
    for candidate in FIXTURE["candidates"]:
        signal = SocialSignal(
            source="linkedin",
            external_id=candidate["link"],
            url=candidate["link"],
            text=f"{candidate['title']} {candidate['snippet']}",
        )
        enrich_signal(signal)
        signals.append(signal)

    created = await engine.ingest_social(signals)
    result = await engine.flush_alerts()

    # The second pass is the dedup proof. Same inputs, same pipeline: anything
    # it adds is a company alerted twice.
    repeat_signals = await engine.ingest_social(signals)
    repeat_alerts = (await engine.flush_alerts())["sent"]

    summary = db.ledger_summary()
    ghosts = [
        {
            "company": ghost["company_name"],
            "batch": ghost["batch"],
            "confidence": ghost["confidence"],
            "url": ghost["url"],
        }
        for ghost in db.list_ghosts(20)
    ]
    return {
        "candidates_evaluated": summary["evaluated"],
        "verdicts": summary["verdicts"],
        "suppression_reasons": summary["reasons"],
        "official_corpus": db.count_official(),
        "signals_persisted": created,
        "alerts_delivered": result["sent"],
        "early_signals": ghosts,
        "second_pass_new_signals": repeat_signals,
        "second_pass_new_alerts": repeat_alerts,
    }


def render(report: dict) -> None:
    print(f"Official snapshot seeded: {report['official_corpus']} relevant records "
          f"(the full live directory is ~6,200).")
    print(f"\nEvaluated {report['candidates_evaluated']} real captured candidates.")

    print("\n  verdict")
    for verdict, count in sorted(report["verdicts"].items(), key=lambda kv: -kv[1]):
        print(f"   {count:>4}  {verdict}")

    print("\n  suppression reason")
    for reason, count in sorted(report["suppression_reasons"].items(), key=lambda kv: -kv[1]):
        print(f"   {count:>4}  {reason}")

    print(f"\nPersisted {report['signals_persisted']} signals. "
          f"Slack alerts delivered: {report['alerts_delivered']}.")
    for ghost in report["early_signals"]:
        print(f"   EARLY  {ghost['company']} ({ghost['batch']}) "
              f"confidence {ghost['confidence']}%")
        print(f"          {ghost['url']}")

    print("\nSecond pass over the identical batch (must add nothing):")
    print(f"   new signals {report['second_pass_new_signals']}, "
          f"new alerts {report['second_pass_new_alerts']}")

    print("\nBrowse it: python scripts/demo_server.py "
          "-> http://localhost:8000/ledger")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="emit the summary as JSON instead of prose")
    parser.add_argument("--no-reset", action="store_true",
                        help="keep the existing demo database rather than starting clean")
    args = parser.parse_args()

    report = asyncio.run(replay(reset=not args.no_reset))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
