"""Official-status resolution: the adjudicator behind every EARLY claim.

Both failure cases below were observed on live data before this was rewritten:
`Nodus` failed to match the official `Nodus Compute`, and `Shepherd (YC S26)`
matched a different, older `Shepherd` from Winter 2021.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database
from app.engine import RadarEngine
from app.extract import enrich_signal
from app.matcher import MIN_PREFIX_CHARS, normalize_batch, resolve_official
from app.models import Company, SocialSignal


def row(id, name, batch=None, domain=None):
    return {
        "id": id,
        "name": name,
        "normalized_name": "".join(c for c in name.lower() if c.isalnum()),
        "batch": batch,
        "domain": domain,
        "url": f"https://yc.example/{id}",
    }


DIRECTORY = [
    row(1, "Nodus Compute", "Fall 2026"),
    row(2, "Shepherd", "Winter 2021"),
    row(3, "Shepherd", "Winter 2024"),
    row(4, "Collar", "Fall 2026"),
    row(5, "Dreamworks Inc", "Fall 2026"),
    row(6, "Polaris AI", "Summer 2026", "polaris.ai"),
    row(7, "Talos", "Fall 2026"),
]


@pytest.mark.parametrize(
    "label,expected",
    [("Fall 2026", "F26"), ("Spring 2026", "X26"), ("SR007", "SR007"), (None, None)],
)
def test_normalize_batch(label, expected):
    assert normalize_batch(label) == expected


def test_founder_shorthand_resolves_within_the_batch():
    """"Nodus" is how the founder writes "Nodus Compute"."""
    check = resolve_official("Nodus", None, DIRECTORY, "F26")
    assert check.match["name"] == "Nodus Compute"
    assert check.method == "batch_prefix"


def test_shorthand_cannot_reach_across_batches():
    assert resolve_official("Nodus", None, DIRECTORY, "S26").match is None
    assert resolve_official("Nodus", None, DIRECTORY, None).match is None


def test_repeated_name_in_a_different_batch_is_a_different_company():
    """The live miss: Shepherd (YC S26) is not Shepherd (Winter 2021)."""
    assert resolve_official("Shepherd", None, DIRECTORY, "S26").match is None
    assert resolve_official("Shepherd", None, DIRECTORY, "W21").match["batch"] == "Winter 2021"


def test_prefix_is_token_wise_not_substring():
    """"Dream" must not swallow "Dreamworks Inc"."""
    assert resolve_official("Dream", None, DIRECTORY, "F26").match is None


def test_short_names_cannot_prefix_match():
    assert len("AI") < MIN_PREFIX_CHARS
    assert resolve_official("AI", None, DIRECTORY, "F26").match is None


def test_domain_beats_name():
    check = resolve_official("Something Else", "https://www.polaris.ai/team", DIRECTORY)
    assert check.match["name"] == "Polaris AI"
    assert check.method == "domain"


def test_unknown_official_batch_is_not_treated_as_a_conflict():
    directory = [row(9, "Polaris AI")]
    assert resolve_official("Polaris AI", None, directory, "F26").match is not None


def test_check_records_what_was_compared():
    check = resolve_official("Nowhere Inc", None, DIRECTORY, "F26")
    assert check.is_early
    payload = check.as_dict()
    assert payload["matched"] is False
    assert payload["records_checked"] == len(DIRECTORY)
    assert payload["batch_scope"] == "F26"
    assert payload["methods_tried"] == ["exact_name", "batch_prefix"]


def test_early_alert_persists_its_receipt(tmp_path):
    database = Database(str(tmp_path / "receipt.db"))
    database.record_snapshot("yc_directory", 6199, mode="full")

    class Silent:
        async def send_ghost(self, s): pass
        async def send_confirmed(self, s, c): pass
        async def send_official(self, c): pass

    engine = RadarEngine(database, Silent())
    signal = SocialSignal(
        "x", "receipt-1", "https://x.com/f/1",
        "Our company Nowhere Inc has been accepted into Y Combinator F26!",
    )
    enrich_signal(signal)
    asyncio.run(engine.ingest_social([signal]))

    stored = database.list_ghosts(1)[0]
    import json
    receipt = json.loads(stored["official_check_json"])
    assert receipt["matched"] is False
    assert receipt["snapshot_size"] == 6199
    assert receipt["snapshot_source"] == "yc_directory"
    assert receipt["snapshot_taken_at"]
    assert receipt["checked_at"]


def test_a_stale_snapshot_downgrades_early_to_possible(tmp_path):
    """Absence from a stale directory is not evidence of being early."""
    database = Database(str(tmp_path / "stale.db"))
    database.record_snapshot(
        "yc_directory",
        6199,
        taken_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )

    class Silent:
        async def send_ghost(self, s): raise AssertionError("must not alert")
        async def send_confirmed(self, s, c): pass
        async def send_official(self, c): pass

    engine = RadarEngine(database, Silent())
    signal = SocialSignal(
        "x", "stale-1", "https://x.com/f/2",
        "Our company Nowhere Inc has been accepted into Y Combinator F26!",
    )
    enrich_signal(signal)
    asyncio.run(engine.ingest_social([signal]))
    asyncio.run(engine.flush_alerts())

    assert database.stats()["ghosts"] == 0
    reasons = database.ledger_summary()["reasons"]
    assert reasons.get("stale_official_snapshot") == 1


def test_spring_batch_aliases_are_the_same_cohort():
    """Live founder posts write Spring 2026 as both "YC X26" and "YC P26"."""
    from app.matcher import batch_identity, batches_match

    assert batch_identity("Spring 2026") == frozenset({"X26", "P26"})
    assert batches_match("Spring 2026", "P26")
    assert batches_match("Spring 2026", "X26")
    assert not batches_match("Fall 2026", "X26")

    directory = [row(1, "Andustry", "Spring 2026")]
    assert resolve_official("Andustry", None, directory, "P26").match is not None
    assert resolve_official("Andustry", None, directory, "X26").match is not None
    assert resolve_official("Andustry", None, directory, "F26").match is None
