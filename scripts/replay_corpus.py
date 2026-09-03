"""Replay real captured search results through the unmodified production pipeline.

This is a rehearsal, not a simulation. Every candidate below is a genuine Serper
result for a public LinkedIn post, captured live and committed as a fixture. They
run through the same extraction, scoring, official matching, dedup and alert code
that production uses -- nothing is hand-fed and no timestamp is invented.

What it demonstrates is the part that matters: of ~139 real candidates, almost all
are correctly suppressed with a recorded reason, and the handful that survive are
companies genuinely absent from the official directory.

    python scripts/demo_seed.py
    python scripts/replay_corpus.py
    python scripts/demo_server.py     # then open http://localhost:8000/ledger
"""

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

from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.engine import RadarEngine  # noqa: E402
from app.extract import enrich_signal  # noqa: E402
from app.models import Company, SocialSignal  # noqa: E402
from app.slack import SlackNotifier  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "linkedin_corpus.json").read_text()
)


async def main():
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
    await engine.ingest_official(official, alert_new=False)
    db.record_snapshot(
        "yc_directory", 6199, mode="full",
        index_used="YCCompany_By_Launch_Date_production",
        active_batches=["Fall 2026", "Summer 2026"],
    )
    print(f"Official snapshot seeded: {len(official)} relevant records "
          f"(full live directory is 6,199).")

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

    summary = db.ledger_summary()
    print(f"\nEvaluated {summary['evaluated']} real captured candidates.")
    print("\n  verdict")
    for verdict, count in sorted(summary["verdicts"].items(), key=lambda kv: -kv[1]):
        print(f"   {count:>4}  {verdict}")
    print("\n  suppression reason")
    for reason, count in summary["reasons"].items():
        print(f"   {count:>4}  {reason}")

    print(f"\nPersisted {created} signals. Slack alerts delivered: {result['sent']}.")
    for ghost in db.list_ghosts(20):
        print(f"   EARLY  {ghost['company_name']} ({ghost['batch']}) "
              f"confidence {ghost['confidence']}%")
        print(f"          {ghost['url']}")

    print("\nRe-running the identical batch (must add nothing):")
    again = await engine.ingest_social(signals)
    second = await engine.flush_alerts()
    print(f"   new signals {again}, new alerts {second['sent']}")
    print("\nOpen http://localhost:8000/ledger after starting the demo server.")


asyncio.run(main())
