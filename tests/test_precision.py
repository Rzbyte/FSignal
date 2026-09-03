"""Precision gate, measured on real captured search results.

The failure this exists to prevent is the one that made the earlier version
unusable: every YC-adjacent post produced an alert, with company names like
'of', 'Rosenbaum posted this - LinkedIn' and a literal sentinel 'Unknown
(Deterministic)'. Passing unit tests said nothing about that, because none of
them ran real search output through the pipeline.

The fixture is 139 genuine Serper results for public LinkedIn posts, captured
live, deliberately including the adversarial families a keyword matcher cannot
tell apart from an announcement: application deadlines, rejection posts, demo-day
recaps, alumni threads, recruiter posts that say "backed by Y Combinator",
investor commentary, and congratulations. Labels were hand-checked.

These thresholds are the product spec. If a change trades precision for volume,
this test fails.
"""

import json
from pathlib import Path

import pytest

from app.config import settings
from app.extract import enrich_signal, valid_company_name
from app.intelligence import assess_signal
from app.matcher import match_official
from app.models import SocialSignal

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "linkedin_corpus.json").read_text()
)
CANDIDATES = FIXTURE["candidates"]
OFFICIAL = FIXTURE["official_snapshot"]

#: Companies that are genuinely absent from the official directory but are real,
#: verified by hand against ycombinator.com at capture time. An alert about one of
#: these is a correct early signal, not a false positive.
#: `Shepherd (YC S26)` is real but the directory only holds Shepherd (Winter 2021)
#: and Shepherd (Winter 2024) -- a name collision, not the same company.
VERIFIED_UNLISTED = {"EVO HQ", "Mantle", "Shepherd"}

#: Page chrome that must never survive into a company name.
CHROME = ("posted this", "linkedin", "'s post", "close menu", "view organization")


def adjudicate(candidate: dict):
    """Run one captured result through the real pipeline, as production does."""
    signal = SocialSignal(
        source="linkedin",
        external_id=candidate["link"],
        url=candidate["link"],
        text=f"{candidate['title']} {candidate['snippet']}",
    )
    enrich_signal(signal)
    extraction = signal.extraction
    if not extraction.is_usable:
        return signal, extraction, None, "suppressed"

    match = match_official(signal.company_name, signal.company_domain, OFFICIAL)
    assess_signal(signal, match)
    if match:
        return signal, extraction, match, "already_official"
    if signal.confidence < settings.min_signal_confidence:
        return signal, extraction, None, "possible"
    return signal, extraction, None, "alerted"


ADJUDICATED = [adjudicate(c) for c in CANDIDATES]
ALERTED = [row for row in ADJUDICATED if row[3] == "alerted"]


def test_corpus_is_large_and_real():
    assert len(CANDIDATES) >= 100
    assert all(c["link"].startswith("https://") for c in CANDIDATES)


def test_no_alert_carries_a_fragment_or_page_chrome_as_a_company():
    """'of', 'Rosenbaum posted this - LinkedIn', 'had the privilege of writing 22'."""
    for signal, _, _, _ in ADJUDICATED:
        if not signal.company_name:
            continue
        assert valid_company_name(signal.company_name), signal.company_name
        low = signal.company_name.lower()
        assert not any(marker in low for marker in CHROME), signal.company_name


def test_no_sentinel_company_survives():
    names = {s.company_name for s, _, _, _ in ADJUDICATED if s.company_name}
    assert not any("unknown" in name.lower() for name in names)


def test_every_suppression_carries_a_reason_code():
    for _, extraction, _, verdict in ADJUDICATED:
        if verdict == "suppressed":
            assert extraction.reason, "a suppressed candidate must say why"


def test_alert_precision_is_at_least_ninety_percent():
    """An alert is correct when it names a company that actually exists."""
    listed = {row["name"] for row in OFFICIAL}
    correct = [
        signal
        for signal, _, _, _ in ALERTED
        if signal.company_name in listed | VERIFIED_UNLISTED
    ]
    assert ALERTED, "the corpus must still produce alerts, or precision is meaningless"
    precision = len(correct) / len(ALERTED)
    assert precision >= 0.90, f"precision {precision:.0%} on {len(ALERTED)} alerts"


def test_noise_budget_is_at_most_one_in_twenty():
    listed = {row["name"] for row in OFFICIAL}
    bad = [
        signal
        for signal, _, _, _ in ALERTED
        if signal.company_name not in listed | VERIFIED_UNLISTED
    ]
    assert len(bad) <= max(1, len(ALERTED) // 20), [s.company_name for s in bad]


def test_the_known_genuine_early_signal_is_retained():
    """EVO HQ (YC F26) announced on LinkedIn and is absent from the directory.

    Over-tightening the gate until nothing survives is the other way to fail.
    """
    alerted = {s.company_name for s, _, _, _ in ALERTED}
    assert "EVO HQ" in alerted


def test_already_listed_companies_never_alert_as_early():
    listed = {row["name"] for row in OFFICIAL}
    for signal, _, match, verdict in ADJUDICATED:
        if verdict == "alerted":
            assert signal.company_name not in listed, signal.company_name


@pytest.mark.parametrize(
    "phrase",
    [
        "rejected from Y Combinator",
        "application deadline",
        "demo day",
        "we are hiring",
        "investment thesis",
        "startup school",
    ],
)
def test_adversarial_families_do_not_alert(phrase):
    """Each of these appears in the captured corpus and must never be an alert."""
    matching = [
        row
        for row in ADJUDICATED
        if phrase.lower() in row[0].text.lower()
    ]
    assert matching, f"corpus no longer covers {phrase!r}"
    for signal, extraction, _, verdict in matching:
        assert verdict != "alerted", f"{phrase!r} alerted as {signal.company_name!r}"


def test_confidence_is_not_saturated():
    """The old scorer put real signals and noise alike in a narrow band near 99."""
    scores = sorted(
        {s.confidence for s, _, _, v in ADJUDICATED if v != "suppressed"}
    )
    assert len(scores) >= 4, scores
    assert max(scores) <= 100 and min(scores) >= 0
