import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database
from app.engine import RadarEngine
from app.extract import enrich_signal, extract
from app.matcher import match_official
from app.models import Company, SocialSignal
from app.pond import PondProtocolError, manifest, terminal, validate_parameters
from app.sources.official import SpeedrunSource, YCDirectorySource
from app.sources.social import LinkedInSource


def test_extract_resolves_a_founder_announcement():
    result = extract("Our company Polaris AI has been accepted into Y Combinator S26!")
    assert result.is_usable
    assert result.program == "yc"
    assert result.company_name == "Polaris AI"
    assert result.batch == "S26"
    assert result.claim_kind == "first_person"


def test_extract_resolves_a_speedrun_announcement():
    result = extract(
        "We're joining a16z Speedrun SR007 this summer at https://munari.example"
    )
    assert result.is_usable
    assert result.program == "speedrun"
    assert result.batch == "SR007"
    assert result.company_domain == "munari.example"


def test_extract_refuses_a_post_with_no_company():
    """The old sentinel made every identity-less post actionable."""
    result = extract("Y Combinator is now accepting applications for YC S26.")
    assert not result.is_usable
    assert result.company_name is None
    assert result.reason is not None


def test_match_is_conservative():
    rows = [
        {
            "id": 1,
            "name": "Polaris AI",
            "normalized_name": "polarisai",
            "domain": "polaris.ai",
        }
    ]
    assert match_official("PolarisAI", None, rows)["id"] == 1
    assert match_official(None, "https://www.polaris.ai/path", rows)["id"] == 1
    assert match_official("Polar AI", None, rows) is None


def test_speedrun_canonical_api_parser():
    """speedrun.a16z.com is a Next.js app that calls this API itself."""
    company = SpeedrunSource.parse_primary_api(
        {"results": [{"slug": "munari-labs", "name": "Munari Labs", "cohort": "SR007",
                      "website_url": "https://munari.example",
                      "founder_set": [{"first_name": "Ada", "last_name": "Lovelace"}]}]}
    )[0]
    assert company.external_id == "munari-labs"
    assert company.batch == "SR007"
    assert company.url == "https://speedrun.a16z.com/companies/munari-labs"
    assert "Ada Lovelace" in company.description


def test_speedrun_fallback_parser():
    """Resilience only -- never evidence that canonical monitoring works."""
    html = '<div><a href="/companies/m">Munari Labs</a><span>a16z sr007</span></div>'
    company = SpeedrunSource.parse_fallback(html)[0]
    assert company.batch == "SR007"


def test_linkedin_search_result_parser_requires_company_identity():
    payload = {
        "organic": [
            {
                "title": "Founder launch announcement",
                "snippet": "Our company Atlas AI has been accepted into Y Combinator S26.",
                "link": "https://www.linkedin.com/posts/example-123",
            },
            {
                "title": "Generic YC news",
                "snippet": "YC S26 is happening now.",
                "link": "https://www.linkedin.com/posts/generic-456",
            },
        ]
    }
    signals = LinkedInSource.parse_response(payload)
    # Sources no longer filter -- the engine adjudicates -- but only one of these
    # can produce a defensible company identity.
    usable = [s for s in signals if s.extraction.is_usable]
    assert len(usable) == 1
    assert usable[0].company_name == "Atlas AI"

    generic = next(s for s in signals if "generic-456" in s.url)
    assert generic.extraction.reason == "no_company_identity"


def fresh_database(path):
    """A database whose official snapshot is current.

    Production always takes an official snapshot before any social scan runs.
    Without one the engine correctly refuses to call anything EARLY, so tests
    that expect a ghost have to set the same precondition.
    """
    database = Database(str(path))
    database.record_snapshot("yc_directory", 1, mode="test")
    database.record_snapshot("speedrun", 1, mode="test")
    return database


def social_signal(*args, **kwargs):
    """Build a signal the way production does: real text through real extraction.

    Constructing a SocialSignal directly and hand-setting `company_name` would
    test a code path that never runs in production, where identity always comes
    from the extractor.
    """
    signal = SocialSignal(*args, **kwargs)
    enrich_signal(signal)
    return signal


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def send_ghost(self, _signal):
        self.events.append("ghost")

    async def send_confirmed(self, _signal, _official):
        self.events.append("confirmed")

    async def send_official(self, _official):
        self.events.append("official")


def test_lifecycle_dedup_and_reconciliation(tmp_path):
    database = fresh_database(tmp_path / "lifecycle.db")
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)
    signal = social_signal(
        "x",
        "1",
        "https://x.com/example/1",
        "Our company Polaris AI has been accepted into Y Combinator F26!",
        detected_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    asyncio.run(engine.ingest_social([signal]))
    asyncio.run(engine.flush_alerts())
    asyncio.run(engine.ingest_social([signal]))
    asyncio.run(engine.flush_alerts())
    assert database.stats()["signals"] == 1
    assert database.stats()["ghosts"] == 1

    asyncio.run(
        engine.ingest_official(
            [Company("Polaris AI", "yc_directory", "p", "https://yc.example/p")]
        )
    )
    assert asyncio.run(engine.reconcile_ghosts()) == 1
    asyncio.run(engine.flush_alerts())
    assert database.stats()["confirmed"] == 1
    assert notifier.events == ["ghost", "confirmed"]


def test_official_incremental_alert_skips_baseline(tmp_path):
    database = Database(str(tmp_path / "official.db"))
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)

    asyncio.run(
        engine.ingest_official(
            [Company("A", "yc_directory", "a", "https://yc.example/a")],
            alert_new=False,
        )
    )
    asyncio.run(
        engine.ingest_official(
            [
                Company("A", "yc_directory", "a", "https://yc.example/a"),
                Company("B", "yc_directory", "b", "https://yc.example/b"),
            ],
            alert_new=True,
        )
    )
    asyncio.run(engine.flush_alerts())
    assert notifier.events == ["official"]


def test_a_delivered_official_alert_leaves_a_timeline_trace(tmp_path):
    """The other three alert kinds record delivery; this one used not to.

    The timeline gate was `if signal:`, and an official alert has no signal
    behind it -- so a NEW OFFICIAL alert was sent with no record of having been
    sent, and `/signals/{id}/timeline` could never account for it.
    """
    database = Database(str(tmp_path / "timeline.db"))
    engine = RadarEngine(database, FakeNotifier())
    company = Company("B", "yc_directory", "b", "https://yc.example/b")

    asyncio.run(engine.ingest_official([], alert_new=False))
    asyncio.run(engine.ingest_official([company], alert_new=True))
    asyncio.run(engine.flush_alerts())

    events = database.timeline(official_company_id=1)
    assert "slack_alert_sent" in {event["event_type"] for event in events}


def test_a_dangling_outbox_row_does_not_mute_the_queue(tmp_path):
    """One unresolvable alert must not hold up every alert behind it.

    A missing target used to raise AttributeError from inside the notifier.
    That is not a per-alert failure, so the flush paused instead of retiring the
    row -- and stayed paused for five attempts, with real alerts waiting.
    """
    database = Database(str(tmp_path / "dangling.db"))
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)

    # Points at an official company that was never written.
    database.enqueue_alert("official:yc_directory:ghostrow", "official",
                           official_company_id=9999)
    asyncio.run(engine.ingest_official([], alert_new=False))
    asyncio.run(
        engine.ingest_official(
            [Company("B", "yc_directory", "b", "https://yc.example/b")],
            alert_new=True,
        )
    )
    result = asyncio.run(engine.flush_alerts())

    assert result["dead"] == 1          # the dangling row is retired, not retried
    assert notifier.events == ["official"]  # the real alert still went out


def test_pond_manifest_and_terminal_are_v1_compatible():
    data = manifest()
    assert data["protocol"] == "marketplace-agent"
    assert data["protocol_version"] == "1.0"
    assert data["metadata"]["category"] == "sales"
    result = terminal("run_123", "ok")
    assert result["status"] == "completed"
    assert result["usage"] == {"unit_of_measurement": "result", "quantity": 1}


def test_pond_action_parameter_validation():
    assert validate_parameters("get_status", {}) == {}
    assert validate_parameters("list_ghosts", {"limit": 5}) == {"limit": 5}

    with pytest.raises(PondProtocolError) as exc:
        validate_parameters("unknown", {})
    assert exc.value.code == "unsupported_operation"

    with pytest.raises(PondProtocolError) as exc:
        validate_parameters("list_ghosts", {"limit": 100})
    assert exc.value.code == "invalid_input"


def test_social_post_for_already_official_company_is_not_early_alert(tmp_path):
    database = Database(str(tmp_path / "already-official.db"))
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)
    asyncio.run(
        engine.ingest_official(
            [Company("Polaris AI", "yc_directory", "polaris", "https://yc.example/polaris")]
        )
    )
    signal = social_signal(
        "x",
        "post-after-directory",
        "https://x.com/example/2",
        "Our company Polaris AI has been accepted into Y Combinator S26!",
        company_name="Polaris AI",
        batch="S26",
        confidence=95,
    )
    asyncio.run(engine.ingest_social([signal]))
    assert notifier.events == []
    assert database.stats()["ghosts"] == 0


class FlakyNotifier(FakeNotifier):
    def __init__(self):
        super().__init__()
        self.failures_left = 1

    async def send_ghost(self, _signal):
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("temporary Slack failure")
        self.events.append("ghost")


def test_alert_outbox_retries_after_transient_slack_failure(tmp_path):
    database = fresh_database(tmp_path / "outbox.db")
    notifier = FlakyNotifier()
    engine = RadarEngine(database, notifier)
    signal = social_signal(
        "x",
        "retry-me",
        "https://x.com/example/retry",
        "Our company Retry AI has been accepted into Y Combinator S26!",
        company_name="Retry AI",
        confidence=95,
    )

    asyncio.run(engine.ingest_social([signal]))
    first = asyncio.run(engine.flush_alerts())
    assert first["failed"] == 1
    assert database.outbox_stats()["pending"] == 1

    # The source post remains deduplicated, but its alert is not lost.
    asyncio.run(engine.ingest_social([signal]))
    second = asyncio.run(engine.flush_alerts())
    assert second["sent"] == 1
    assert database.outbox_stats()["pending"] == 0
    assert notifier.events == ["ghost"]


def _valid_pond_run(**overrides):
    body = {
        "run_id": "run_test_1",
        "agent_id": "agt_test",
        "conversation_id": "chat_test",
        "history_truncated": False,
        "action_id": "get_status",
        "user": {
            "id": "usr_test",
            "locale": "en-US",
            "timezone": "Asia/Jakarta",
        },
        "messages": [
            {
                "id": "msg_test",
                "role": "user",
                "created_at": "2026-09-03T10:00:00Z",
                "parts": [{"type": "text", "text": "Show current monitor status."}],
            }
        ],
        "parameters": {},
        "execution": {
            "accepted_output_modes": ["text/markdown"],
            "deadline_ms": 30_000,
        },
    }
    body.update(overrides)
    return body


def test_pond_run_request_validates_complete_v1_envelope():
    from app.pond import PondRunRequest

    run = PondRunRequest.model_validate(_valid_pond_run())
    assert run.run_id == "run_test_1"
    assert run.messages[0].role == "user"


def test_pond_run_request_rejects_incompatible_output_mode_and_deadline():
    from pydantic import ValidationError
    from app.pond import PondRunRequest

    bad_mode = _valid_pond_run()
    bad_mode["execution"] = {
        "accepted_output_modes": ["application/json"],
        "deadline_ms": 30_000,
    }
    with pytest.raises(ValidationError):
        PondRunRequest.model_validate(bad_mode)

    bad_deadline = _valid_pond_run()
    bad_deadline["execution"] = {
        "accepted_output_modes": ["text/markdown"],
        "deadline_ms": 301_000,
    }
    with pytest.raises(ValidationError):
        PondRunRequest.model_validate(bad_deadline)


def test_pond_run_request_rejects_attachments_when_manifest_disables_them():
    from pydantic import ValidationError
    from app.pond import PondRunRequest

    body = _valid_pond_run()
    body["messages"][0]["parts"].append(
        {
            "type": "file",
            "file": {
                "url": "https://example.com/input.txt",
                "name": "input.txt",
                "media_type": "text/plain",
            },
        }
    )
    with pytest.raises(ValidationError):
        PondRunRequest.model_validate(body)


def test_explainable_intelligence_scores_early_founder_signal():
    from app.intelligence import assess_signal

    signal = social_signal(
        source="x",
        external_id="intel-1",
        url="https://x.com/founder/status/1",
        text=(
            "Our company LedgerFox has been accepted into Y Combinator S26. "
            "We're building B2B treasury and payments infrastructure. https://ledgerfox.ai"
        ),
        author_name="Jane Founder",
        author_handle="janefounder",
        company_name="LedgerFox",
        company_domain="ledgerfox.ai",
        batch="S26",
        program="yc",
    )
    assess_signal(signal, None)

    assert signal.confidence >= 80
    assert signal.confidence_label == "high"
    assert signal.gtm_score >= 80
    assert signal.gtm_priority == "high"
    assert any("No matching official-directory" in item for item in signal.evidence)
    assert any("Finance/operations" in item for item in signal.gtm_reasons)


def test_intelligence_and_timeline_are_persisted(tmp_path):
    database = fresh_database(tmp_path / "timeline.db")
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)
    signal = social_signal(
        "x",
        "timeline-1",
        "https://x.com/founder/status/timeline-1",
        "Our company LedgerFox has been accepted into Y Combinator S26 for B2B payments.",
        author_handle="founder",
        company_name="LedgerFox",
        company_domain="ledgerfox.ai",
        batch="S26",
    )

    asyncio.run(engine.ingest_social([signal]))
    row = database.list_ghosts(1)[0]
    assert row["confidence_label"] == "high"
    assert row["gtm_priority"] == "high"
    assert "official-directory" in row["evidence_json"]

    asyncio.run(engine.flush_alerts())
    timeline = database.timeline(row["id"])
    assert [event["event_type"] for event in timeline] == [
        "social_detected",
        "ghost_classified",
        "slack_alert_sent",
    ]


def test_timeline_records_later_official_confirmation(tmp_path):
    database = fresh_database(tmp_path / "timeline-confirm.db")
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)
    signal = social_signal(
        "x",
        "timeline-confirm",
        "https://x.com/founder/status/confirm",
        "Our company Atlas Ops has been accepted into Y Combinator S26.",
        company_name="Atlas Ops",
        batch="S26",
        detected_at=datetime.now(timezone.utc) - timedelta(hours=4),
    )
    asyncio.run(engine.ingest_social([signal]))
    signal_id = database.list_ghosts(1)[0]["id"]
    asyncio.run(engine.ingest_official([
        Company("Atlas Ops", "yc_directory", "atlas-ops", "https://yc.example/atlas")
    ]))
    assert asyncio.run(engine.reconcile_ghosts()) == 1
    events = [event["event_type"] for event in database.timeline(signal_id)]
    assert "official_confirmed" in events
    assert database.stats()["average_early_lead_hours"] is not None


def test_pond_get_timeline_parameter_validation():
    assert validate_parameters("get_timeline", {"signal_id": 7}) == {"signal_id": 7}
    with pytest.raises(PondProtocolError):
        validate_parameters("get_timeline", {})
    with pytest.raises(PondProtocolError):
        validate_parameters("get_timeline", {"signal_id": 0})


# ── HARDENING TESTS ────────────────────────────────────────────────────────────


def test_restart_safe_dedup(tmp_path):
    db_path = str(tmp_path / 'restart.db')
    db1 = fresh_database(db_path)
    notifier1 = FakeNotifier()
    engine1 = RadarEngine(db1, notifier1)
    signal = social_signal(
        'x', 'restart-1', 'https://x.com/e/restart-1',
        'Our company RestartCo has been accepted into Y Combinator S26!',
        company_name='RestartCo', confidence=90,
    )
    asyncio.run(engine1.ingest_social([signal]))
    asyncio.run(engine1.flush_alerts())
    assert notifier1.events == ['ghost']

    db2 = Database(db_path)
    notifier2 = FakeNotifier()
    engine2 = RadarEngine(db2, notifier2)
    asyncio.run(engine2.ingest_social([signal]))
    asyncio.run(engine2.flush_alerts())

    assert db2.stats()['signals'] == 1
    assert db2.outbox_stats()['pending'] == 0
    assert notifier2.events == []


def test_repeated_confirmation_dedup(tmp_path):
    database = fresh_database(tmp_path / 'reconfirm.db')
    notifier = FakeNotifier()
    engine = RadarEngine(database, notifier)
    signal = social_signal(
        'x', 'reconfirm-1', 'https://x.com/e/reconfirm-1',
        'Our company DualCo has been accepted into Y Combinator S26!',
        company_name='DualCo', confidence=90,
        detected_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )
    asyncio.run(engine.ingest_social([signal]))
    asyncio.run(engine.ingest_official([
        Company('DualCo', 'yc_directory', 'dualco', 'https://yc.example/dualco')
    ]))
    count1 = asyncio.run(engine.reconcile_ghosts())
    asyncio.run(engine.flush_alerts())
    assert count1 == 1
    assert 'confirmed' in notifier.events
    count2 = asyncio.run(engine.reconcile_ghosts())
    asyncio.run(engine.flush_alerts())
    assert count2 == 0
    assert notifier.events.count('confirmed') == 1


def test_source_failure_isolation(tmp_path):
    database = Database(str(tmp_path / 'isolation.db'))

    class BrokenSource:
        name = 'broken_src'
        async def collect(self):
            raise RuntimeError('simulated failure')

    class GoodSource:
        name = 'good_src'
        async def collect(self):
            return [Company('GoodCo', 'good_src', 'goodco', 'https://good.example')]

    from app.scanner import Scanner
    s = Scanner(database)
    s.official_sources = [BrokenSource(), GoodSource()]
    s.social_sources = []
    result = asyncio.run(s.scan_all())
    assert result['official']['broken_src']['status'] == 'error'
    assert result['official']['good_src']['status'] == 'ok'
    statuses = {row['source']: row['status'] for row in database.source_status()}
    assert statuses['broken_src'] == 'error'
    assert statuses['good_src'] == 'ok'


def test_pond_idempotency_survives_restart(tmp_path):
    import json as _json
    db_path = str(tmp_path / 'pond_idem.db')
    db1 = Database(db_path)
    run_id = 'pond-idem-1'
    body_hash = 'deadbeef'
    saved = {'run_id': run_id, 'status': 'completed',
              'output': [{'type': 'text', 'text': 'cached'}],
              'usage': {'unit_of_measurement': 'result', 'quantity': 1}}
    db1.save_pond_run(run_id, body_hash, saved)
    db2 = Database(db_path)
    replayed = db2.get_pond_run(run_id)
    assert replayed is not None
    assert _json.loads(replayed['response_json'])['status'] == 'completed'
    assert replayed['request_hash'] == body_hash


def test_scoring_is_deterministic():
    from app.intelligence import assess_signal

    def make_signal():
        return social_signal(
            source='x', external_id='det-1', url='https://x.com/f/1',
            text='Our company Kestrel AI has been accepted into Y Combinator S26. B2B payments. https://kestrel.ai',
            author_handle='kestrelfounder', company_name='Kestrel AI',
            company_domain='kestrel.ai', batch='S26', program='yc',
        )

    s1 = make_signal()
    assess_signal(s1, None)
    s2 = make_signal()
    assess_signal(s2, None)
    assert s1.confidence == s2.confidence
    assert s1.confidence_label == s2.confidence_label
    assert s1.gtm_score == s2.gtm_score
    assert s1.gtm_priority == s2.gtm_priority
    assert s1.evidence == s2.evidence
    assert s1.gtm_reasons == s2.gtm_reasons


def test_health_db_helpers_never_raise(tmp_path):
    database = Database(str(tmp_path / 'health_empty.db'))
    stats = database.stats()
    assert stats['ghosts'] == 0
    assert stats['confirmed'] == 0
    assert stats['signals'] == 0
    assert stats['average_early_lead_hours'] is None
    outbox = database.outbox_stats()
    assert outbox['pending'] == 0
    assert outbox['sent'] == 0
    assert database.source_status() == []
    assert database.list_ghosts(10) == []


# ── SCHEDULER TESTS ────────────────────────────────────────────────────────────


def test_source_state_backoff_no_failures():
    from app.scheduler import SourceState
    state = SourceState('x', interval_seconds=600)
    assert state.backoff_seconds() == 600


def test_source_state_backoff_one_failure():
    from app.scheduler import SourceState
    state = SourceState('x', interval_seconds=600)
    state.consecutive_failures = 1
    # 1 failure: still normal interval (single transient errors not penalised)
    assert state.backoff_seconds() == 600


def test_source_state_backoff_two_failures():
    from app.scheduler import SourceState
    state = SourceState('x', interval_seconds=600)
    state.consecutive_failures = 2
    assert state.backoff_seconds() == 1200  # 2x


def test_source_state_backoff_three_failures():
    from app.scheduler import SourceState
    state = SourceState('x', interval_seconds=600)
    state.consecutive_failures = 3
    assert state.backoff_seconds() == 2400  # 4x


def test_source_state_backoff_capped():
    from app.scheduler import MAX_BACKOFF_SECONDS, SourceState
    state = SourceState('x', interval_seconds=600)
    state.consecutive_failures = 100
    assert state.backoff_seconds() == MAX_BACKOFF_SECONDS


def test_source_state_health_label_pending():
    from app.scheduler import SourceState
    state = SourceState('x', 600)
    assert state.health_label == 'pending'


def test_source_state_health_label_healthy():
    from app.scheduler import SourceState
    state = SourceState('x', 600)
    state.last_run = datetime.now(timezone.utc)
    state.last_success = datetime.now(timezone.utc)
    assert state.health_label == 'healthy'


def test_source_state_health_label_not_configured():
    from app.scheduler import SourceState
    state = SourceState('x', 600)
    state.last_run = datetime.now(timezone.utc)
    # No last_success and 0 failures means credentials were absent
    assert state.health_label == 'not_configured'


def test_source_state_health_label_degraded():
    from app.scheduler import SourceState
    state = SourceState('x', 600)
    state.last_run = datetime.now(timezone.utc)
    state.consecutive_failures = 2
    assert state.health_label == 'degraded'


def test_source_state_as_dict_keys():
    from app.scheduler import SourceState
    state = SourceState('yc_directory', 1200)
    d = state.as_dict()
    for key in ('source', 'health', 'interval_minutes', 'last_run',
                'last_success', 'next_run', 'seconds_until_next', 'consecutive_failures'):
        assert key in d, f'Missing key: {key}'
    assert d['source'] == 'yc_directory'
    assert d['interval_minutes'] == 20.0


def test_metered_sources_are_paced_slower_than_free_ones():
    """The property, not the literals.

    Every social scan spends search credits; the directories cost nothing. A
    social interval set as tight as a directory one burns a free search plan in
    about two days and takes the bot down with it, which is a worse failure than
    finding a signal an hour later. Asserting the exact minute values instead
    just meant this test had to be edited every time they were tuned.
    """
    from app.config import settings

    metered = min(
        settings.x_scan_interval_minutes, settings.linkedin_scan_interval_minutes
    )
    free = max(
        settings.yc_scan_interval_minutes, settings.speedrun_scan_interval_minutes
    )
    assert metered >= free
    # The brief allows an eight-hour cadence; going slower than that stops being
    # the continuous monitor it asks for.
    assert metered <= 8 * 60
    # Reconciliation is local work against an already-fetched snapshot, so it has
    # no reason to lag the directory that feeds it.
    assert settings.ghost_recheck_interval_minutes <= free


def test_per_source_scheduler_from_config(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    from app.scheduler import PerSourceScheduler
    db = Database(str(tmp_path / 'sched.db'))
    s = Scanner(db)
    sched = PerSourceScheduler.from_config(s)
    assert set(sched.states.keys()) == {
        'yc_directory', 'speedrun', 'x', 'linkedin', 'ghost_reconciliation'
    }
    from app.config import settings
    # Configured minutes reach the scheduler as seconds, per source.
    for name, minutes in (
        ('x', settings.x_scan_interval_minutes),
        ('linkedin', settings.linkedin_scan_interval_minutes),
        ('yc_directory', settings.yc_scan_interval_minutes),
        ('speedrun', settings.speedrun_scan_interval_minutes),
        ('ghost_reconciliation', settings.ghost_recheck_interval_minutes),
    ):
        assert sched.states[name].interval_seconds == minutes * 60


def test_run_named_source_unknown_raises(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    db = Database(str(tmp_path / 'ns.db'))
    s = Scanner(db)
    with pytest.raises(ValueError, match='Unknown source'):
        asyncio.run(s.run_named_source('nonexistent_source'))


def test_run_named_source_good_source(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    db = Database(str(tmp_path / 'rns.db'))

    class GoodOfficialSource:
        name = 'good_official'
        async def collect(self):
            return [Company('RnsTestCo', 'good_official', 'rnstestco', 'https://rns.example')]

    s = Scanner(db)
    s.official_sources = [GoodOfficialSource()]
    s.social_sources = []
    result = asyncio.run(s.run_named_source('good_official'))
    assert result['status'] == 'ok'
    assert result['items'] == 1


def test_run_reconciliation_end_to_end(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    db = Database(str(tmp_path / 'recon.db'))
    notifier = FakeNotifier()

    class GrowingDirectory:
        """The directory as it really behaves: ReconCo shows up on a later scan."""
        name = 'yc_directory'

        def __init__(self):
            self.scans = 0

        async def collect(self):
            self.scans += 1
            companies = [Company('Existing', 'yc_directory', 'existing', 'https://yc.example/e')]
            if self.scans > 1:
                companies.append(
                    Company('ReconCo', 'yc_directory', 'reconco', 'https://yc.example/reconco')
                )
            return companies

    s = Scanner(db)
    s.official_sources = [GrowingDirectory()]
    s.social_sources = []
    s.engine.notifier = notifier

    # Baseline snapshot first, exactly as scan_all orders it: a social post can
    # only be judged early against a directory we have actually read.
    asyncio.run(s.run_named_source('yc_directory'))

    signal = social_signal(
        'x', 'recon-sched-1', 'https://x.com/e/recon-sched-1',
        'Our company ReconCo has been accepted into Y Combinator S26!',
        detected_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    asyncio.run(s.engine.ingest_social([signal]))
    assert db.stats()['ghosts'] == 1

    # ReconCo now appears in the directory.
    asyncio.run(s.run_named_source('yc_directory'))

    # Reconciliation should find and confirm the ghost
    result = asyncio.run(s.run_reconciliation())
    assert result['reconciled'] >= 1


def test_scheduler_run_once_failure_increments_state(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    from app.scheduler import PerSourceScheduler, SourceState
    db = Database(str(tmp_path / 'fail.db'))

    class AlwaysBreaks:
        name = 'broken_src'
        async def collect(self):
            raise RuntimeError('simulated network error')

    s = Scanner(db)
    s.official_sources = [AlwaysBreaks()]
    s.social_sources = []

    state = SourceState('broken_src', interval_seconds=60)
    sched = PerSourceScheduler(s, {'broken_src': state})

    async def run():
        sched._stop = asyncio.Event()
        await sched._run_once(state)

    asyncio.run(run())
    assert state.consecutive_failures == 1
    assert state.next_run is not None
    assert state.last_success is None


def test_scheduler_run_once_not_configured_does_not_increment_state(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    from app.scheduler import PerSourceScheduler, SourceState
    db = Database(str(tmp_path / 'nc.db'))

    class NotConfiguredSource:
        name = 'nc_src'
        async def collect(self):
            raise RuntimeError('not_configured: X_BEARER_TOKEN not set')

    s = Scanner(db)
    s.official_sources = []
    s.social_sources = [NotConfiguredSource()]

    state = SourceState('nc_src', interval_seconds=60)
    sched = PerSourceScheduler(s, {'nc_src': state})

    async def run():
        sched._stop = asyncio.Event()
        await sched._run_once(state)

    asyncio.run(run())
    # not_configured is expected — should not penalise with consecutive_failures
    assert state.consecutive_failures == 0
    assert state.next_run is not None


def test_scheduler_ghost_reconciliation_run_once(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    from app.scheduler import PerSourceScheduler, SourceState
    db = Database(str(tmp_path / 'ghost_recon.db'))

    s = Scanner(db)
    s.official_sources = []
    s.social_sources = []

    state = SourceState('ghost_reconciliation', interval_seconds=600)
    sched = PerSourceScheduler(s, {'ghost_reconciliation': state})

    async def run():
        sched._stop = asyncio.Event()
        await sched._run_once(state)

    asyncio.run(run())
    # Should succeed (0 ghosts to reconcile is still a success)
    assert state.consecutive_failures == 0
    assert state.last_success is not None
    assert state.next_run is not None


def test_dedup_unaffected_after_scheduler_refactor(tmp_path):
    from app.db import Database
    from app.scanner import Scanner
    db = Database(str(tmp_path / 'dedup_sched.db'))

    class RepeatingOfficialSource:
        name = 'yc_directory'
        async def collect(self):
            return [
                Company('DedupeSchedCo', 'yc_directory', 'dedupeschedco', 'https://yc.example/dedupeschedco'),
            ]

    s = Scanner(db)
    s.official_sources = [RepeatingOfficialSource()]
    s.social_sources = []

    # First run establishes baseline; 2nd and 3rd have same company, 0 new items.
    r1 = asyncio.run(s.run_named_source('yc_directory'))
    r2 = asyncio.run(s.run_named_source('yc_directory'))
    r3 = asyncio.run(s.run_named_source('yc_directory'))
    assert r1['status'] == r2['status'] == r3['status'] == 'ok'
    # No social signals from official-source runs
    assert db.stats()['signals'] == 0
    # Official source is recognised after first run
    assert db.has_official_source('yc_directory')
    # Latest source_run must be ok (dedup did not cause an error)
    statuses = {row['source']: row for row in db.source_status()}
    assert statuses['yc_directory']['status'] == 'ok'

def test_yc_parser():
    # Restored test testing the new parser but keeping coverage logic
    hits = [{"slug": "acme", "name": "Acme", "batch": "Summer 2026"}]
    company = YCDirectorySource.parse_algolia(hits)[0]
    assert company.name == "Acme"
    assert company.batch == "Summer 2026"
    assert company.url == "https://www.ycombinator.com/companies/acme"
