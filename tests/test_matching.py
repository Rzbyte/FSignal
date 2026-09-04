"""Official-status resolution: the adjudicator behind every EARLY claim.

Both failure cases below were observed on live data before this was rewritten:
`Nodus` failed to match the official `Nodus Compute`, and `Shepherd (YC S26)`
matched a different, older `Shepherd` from Winter 2021.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database, normalize_name
from app.engine import RadarEngine
from app.extract import enrich_signal
from app.matcher import (
    MIN_PREFIX_CHARS,
    handle_identity,
    normalize_batch,
    resolve_official,
)
from app.models import SocialSignal


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
    assert payload["methods_tried"] == ["exact_name", "batch_prefix", "handle"]


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


# --------------------------------------------------------------------------- #
# Handles are not names                                                        #
# --------------------------------------------------------------------------- #


def _batch(names, batch="Fall 2026"):
    return [
        {
            "id": index,
            "name": name,
            "normalized_name": normalize_name(name),
            "batch": batch,
            "domain": None,
        }
        for index, name in enumerate(names)
    ]


def test_a_handle_resolves_to_the_company_it_names():
    """The failure this guards against was found on the brief's own example.

    The task names `x.com/beknabdik` as the kind of founder to catch. That
    account builds Speko, which the directory lists under Summer 2026 -- but the
    post identifies the company as `@speko_ai`, and comparing that to `Speko` as
    a *name* says they are different companies. FSignal would have announced an
    already-listed company as an early discovery, which is the one failure a
    reader can disprove in ten seconds.
    """
    check = resolve_official("@speko_ai", None, _batch(["Speko"], "Summer 2026"), "S26")
    assert check.match is not None
    assert check.match["name"] == "Speko"
    assert check.method == "handle"


@pytest.mark.parametrize(
    "candidate,official",
    [
        ("@speko_ai", "Speko"),          # separated suffix
        ("spekoai", "Speko"),            # fused suffix
        ("@tryspeko", "Speko"),          # vanity prefix
        ("speko.ai", "Speko"),           # the domain written as a name
        ("@quippy_app", "Quippy"),
        ("@lambda_ai", "Lambda Robotics"),  # a handle *and* a shortening
    ],
)
def test_handle_forms_reach_their_company(candidate, official):
    check = resolve_official(candidate, None, _batch([official]), "F26")
    assert check.match is not None, f"{candidate} should reach {official}"


def test_a_handle_never_reaches_a_company_it_does_not_name():
    check = resolve_official("@unrelated_ai", None, _batch(["Speko", "Quippy"]), "F26")
    assert check.match is None
    assert "handle" in check.methods_tried


def test_a_handle_cannot_reach_across_batches():
    """Same guard as the prefix rule: reduction is only safe inside one cohort."""
    rows = _batch(["Speko"], "Summer 2025")
    assert resolve_official("@speko_ai", None, rows, "F26").match is None


def test_a_handle_with_no_batch_is_never_reduced_against_the_directory():
    """Without a batch there is no scope to make the reduction safe."""
    check = resolve_official("@speko_ai", None, _batch(["Speko"]), None)
    assert check.match is None
    assert "handle" not in check.methods_tried


@pytest.mark.parametrize(
    "name,expected",
    [
        ("@tryStudioai", "studio"),   # not "stud": the cascade used to eat "io"
        ("Studio", "studio"),
        ("Radio", "radio"),
        ("Audio Labs", "audio"),
        ("Lambda Robotics", "lambdarobotics"),  # a real word is not an affix
        ("@evo_hq", ""),              # nothing long enough survives
        ("AI", ""),
        ("@x_ai", ""),
    ],
)
def test_reduction_keeps_real_words_intact(name, expected):
    assert handle_identity(name) == expected


def test_the_receipt_records_the_handle_attempt():
    """An EARLY claim has to say every comparison it made, including this one."""
    check = resolve_official("@unlisted_ai", None, _batch(["Speko"]), "F26")
    assert check.as_dict()["methods_tried"] == ["exact_name", "batch_prefix", "handle"]
