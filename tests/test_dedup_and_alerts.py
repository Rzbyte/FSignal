"""Alerting is per company, not per post -- and one bad alert cannot mute the rest.

Post-level dedup alone meant one founder posting on X and LinkedIn produced two
"EARLY" alerts, and three people congratulating the same company produced three.
From the GTM user's seat that is the difference between a tool and a nuisance.
"""

import asyncio
import json

import pytest

from app.db import Database
from app.engine import RadarEngine
from app.extract import enrich_signal
from app.matcher import company_key
from app.models import Company, SocialSignal
from app.slack import official_receipt


class Recorder:
    def __init__(self):
        self.ghosts, self.corroborations, self.confirmed, self.official = [], [], [], []
        self.counter = 0

    async def send_ghost(self, signal):
        self.ghosts.append(signal)
        self.counter += 1
        return {"ok": True, "ts": f"111.{self.counter}"}

    async def send_corroboration(self, signal, thread_ts):
        self.corroborations.append((signal, thread_ts))
        return {"ok": True}

    async def send_confirmed(self, signal, company):
        self.confirmed.append((signal, company))
        return {"ok": True}

    async def send_official(self, company):
        self.official.append(company)
        return {"ok": True}


def radar(tmp_path, name="dedup.db"):
    database = Database(str(tmp_path / name))
    database.record_snapshot("yc_directory", 6199, mode="full")
    database.record_snapshot("speedrun", 261, mode="canonical")
    recorder = Recorder()
    return database, recorder, RadarEngine(database, recorder)


def signal(source, external_id, text, **kwargs):
    built = SocialSignal(source, external_id, f"https://{source}.example/{external_id}", text, **kwargs)
    enrich_signal(built)
    return built


ANNOUNCEMENT = "Our company Polaris AI has been accepted into Y Combinator F26!"


def test_company_key_prefers_domain_then_scopes_by_batch():
    assert company_key("Polaris AI", "https://polaris.ai/x") == "domain:polaris.ai"
    assert company_key("Polaris AI", None, "yc", "F26") == "yc:F26:polarisai"
    # Same name, different batch, therefore a different company.
    assert company_key("Shepherd", None, "yc", "S26") != company_key("Shepherd", None, "yc", "W21")


def test_one_company_across_two_sources_alerts_once(tmp_path):
    database, recorder, engine = radar(tmp_path)

    asyncio.run(engine.ingest_social([signal("x", "1", ANNOUNCEMENT)]))
    asyncio.run(engine.flush_alerts())
    asyncio.run(engine.ingest_social([signal("linkedin", "2", ANNOUNCEMENT)]))
    asyncio.run(engine.flush_alerts())

    assert len(recorder.ghosts) == 1
    assert len(recorder.corroborations) == 1
    # The corroboration replies in the first alert's thread.
    _, thread_ts = recorder.corroborations[0]
    assert thread_ts == "111.1"
    # Both posts are still persisted as evidence.
    assert database.stats()["signals"] == 2


def test_three_posts_about_one_company_produce_one_alert(tmp_path):
    database, recorder, engine = radar(tmp_path)
    posts = [signal("x", str(i), ANNOUNCEMENT) for i in range(3)]
    asyncio.run(engine.ingest_social(posts))
    asyncio.run(engine.flush_alerts())
    assert len(recorder.ghosts) == 1
    assert len(recorder.corroborations) == 2


def test_confirmation_fires_once_per_company(tmp_path):
    database, recorder, engine = radar(tmp_path)
    asyncio.run(engine.ingest_social([
        signal("x", "1", ANNOUNCEMENT),
        signal("linkedin", "2", ANNOUNCEMENT),
    ]))
    asyncio.run(engine.flush_alerts())

    asyncio.run(engine.ingest_official([
        Company("Polaris AI", "yc_directory", "polaris", "https://yc.example/p", batch="Fall 2026")
    ]))
    asyncio.run(engine.reconcile_ghosts())
    asyncio.run(engine.flush_alerts())

    assert len(recorder.confirmed) == 1


def test_restart_and_rescan_is_silent(tmp_path):
    database, recorder, engine = radar(tmp_path, "restart.db")
    posts = [signal("x", "1", ANNOUNCEMENT)]
    asyncio.run(engine.ingest_social(posts))
    asyncio.run(engine.flush_alerts())
    assert len(recorder.ghosts) == 1

    reopened = Database(str(tmp_path / "restart.db"))
    recorder2 = Recorder()
    engine2 = RadarEngine(reopened, recorder2)
    asyncio.run(engine2.ingest_social([signal("x", "1", ANNOUNCEMENT)]))
    asyncio.run(engine2.flush_alerts())
    assert recorder2.ghosts == []
    assert recorder2.corroborations == []


# --------------------------------------------------------------------------- #
# Outbox                                                                       #
# --------------------------------------------------------------------------- #


class PoisonNotifier(Recorder):
    """Fails permanently for one specific signal, succeeds for everything else."""

    async def send_ghost(self, signal):
        if signal.get("external_id") == "poison":
            raise RuntimeError("invalid_blocks")
        return await super().send_ghost(signal)


def test_a_poison_alert_does_not_block_the_queue(tmp_path):
    database = Database(str(tmp_path / "poison.db"))
    database.record_snapshot("yc_directory", 6199, mode="full")
    notifier = PoisonNotifier()
    engine = RadarEngine(database, notifier)

    asyncio.run(engine.ingest_social([
        signal("x", "poison", "Our company Poison Co has been accepted into Y Combinator F26!"),
        signal("x", "healthy", "Our company Healthy Co has been accepted into Y Combinator F26!"),
    ]))
    for _ in range(6):
        asyncio.run(engine.flush_alerts())

    delivered = {s["company_name"] for s in notifier.ghosts}
    assert "Healthy Co" in delivered, "a permanent failure must not mute later alerts"

    stats = database.outbox_stats()
    assert stats["dead"] >= 1
    assert stats["pending"] == 0


def test_transient_failure_is_retried_not_dead_lettered(tmp_path):
    database = Database(str(tmp_path / "transient.db"))
    database.record_snapshot("yc_directory", 6199, mode="full")

    class Flaky(Recorder):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def send_ghost(self, signal):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("connection reset")
            return await super().send_ghost(signal)

    notifier = Flaky()
    engine = RadarEngine(database, notifier)
    asyncio.run(engine.ingest_social([signal("x", "1", ANNOUNCEMENT)]))

    asyncio.run(engine.flush_alerts())
    assert notifier.ghosts == []
    assert database.outbox_stats()["pending"] == 1

    asyncio.run(engine.flush_alerts())
    assert len(notifier.ghosts) == 1
    assert database.outbox_stats()["pending"] == 0


# --------------------------------------------------------------------------- #
# Slack rendering                                                              #
# --------------------------------------------------------------------------- #


def test_receipt_names_the_corpus_and_the_moment():
    line = official_receipt(
        {
            "official_check_json": json.dumps(
                {
                    "matched": False,
                    "methods_tried": ["exact_name", "batch_prefix"],
                    "batch_scope": "F26",
                    "snapshot_size": 6199,
                    "snapshot_source": "yc_directory",
                    "snapshot_taken_at": "2026-09-04T14:02:00+00:00",
                }
            )
        }
    )
    assert "6,199 YC records" in line
    assert "14:02 UTC" in line
    assert "no exact-name match" in line
    assert "no in-batch prefix match" in line
    assert "batch scope F26" in line


@pytest.mark.anyio
async def test_early_alert_block_kit_is_well_formed(tmp_path, monkeypatch):
    from dataclasses import replace

    import app.slack as slack_module
    from app.config import settings as base

    monkeypatch.setattr(
        slack_module, "settings", replace(base, demo_mode=True, database_path=str(tmp_path / "d.db"))
    )
    notifier = slack_module.SlackNotifier()
    payload = None

    async def capture(title, blocks, thread_ts=None):
        nonlocal payload
        payload = {"text": title, "blocks": blocks}
        return {"ok": True, "ts": "1.1"}

    monkeypatch.setattr(notifier, "_send", capture)
    await notifier.send_ghost(
        {
            "company_name": "EVO HQ",
            "program": "yc",
            "source": "linkedin",
            "batch": "F26",
            "confidence": 70,
            "confidence_label": "likely",
            "gtm_score": 85,
            "gtm_priority": "high",
            "url": "https://linkedin.com/posts/x",
            "text": "EVO HQ (YC F26) got into Y Combinator!!",
            "detected_at": "2026-09-04T16:14:00+00:00",
            "evidence_json": json.dumps(["Company identity extracted: EVO HQ"]),
            "gtm_reasons_json": json.dumps(["Pre-directory timing advantage"]),
            "official_check_json": json.dumps(
                {"matched": False, "methods_tried": ["exact_name"], "snapshot_size": 6199,
                 "snapshot_source": "yc_directory", "snapshot_taken_at": "2026-09-04T14:02:00+00:00"}
            ),
        }
    )

    types = [block["type"] for block in payload["blocks"]]
    assert types[0] == "header"
    assert "section" in types and "actions" in types and "context" in types
    assert "EVO HQ" in payload["text"]
    # Slack limits: 10 fields per section, 2000 chars each, 3000 per text block.
    for block in payload["blocks"]:
        for field in block.get("fields", []):
            assert len(field["text"]) <= 2000
        if "text" in block and isinstance(block["text"], dict):
            assert len(block["text"]["text"]) <= 3000
    # The official check is a full section, not a footnote: it is the credibility
    # block, so it must carry visual weight.
    sections = [
        b["text"]["text"]
        for b in payload["blocks"]
        if b["type"] == "section" and isinstance(b.get("text"), dict)
    ]
    assert any("OFFICIAL CHECK" in t and "6,199 YC records" in t for t in sections)
