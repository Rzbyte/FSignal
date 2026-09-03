"""Social targeting derives its vocabulary from the official directory.

The failure this guards against is subtle and fatal: a batch that is already
fully published cannot produce an early signal, so a monitor pointed at a stale
batch string keeps returning results that are all already listed. Every helper
here exists so no batch label is ever hardcoded in a query.
"""

import pytest

from dataclasses import replace

from app.config import settings as app_settings
from app.db import Database
from app.sources.social import LinkedInSource, XSource
from app.targeting import (
    SocialTargets,
    batch_code,
    cohort_code,
    linkedin_queries,
    speedrun_cohort_phrases,
    x_queries,
    yc_batch_phrases,
)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Winter 2027", "W27"),
        ("Spring 2026", "X26"),   # YC's spring code is X, not Sp
        ("Summer 2026", "S26"),
        ("Fall 2026", "F26"),
        ("fall 2026", "F26"),
        ("Unspecified", None),    # real label in the live directory
        ("", None),
        (None, None),
    ],
)
def test_batch_code(label, expected):
    assert batch_code(label) == expected


@pytest.mark.parametrize(
    "label,expected",
    [("SR007", "SR007"), ("sr7", "SR007"), ("SR 12", "SR012"), ("Fall 2026", None)],
)
def test_cohort_code(label, expected):
    assert cohort_code(label) == expected


def test_phrases_preserve_recency_order_and_dedupe():
    assert yc_batch_phrases(["Fall 2026", "Fall 2026", "Unspecified"]) == [
        "YC F26",
        "Y Combinator F26",
    ]
    assert speedrun_cohort_phrases(["SR007"]) == ["Speedrun SR007", "SR007"]


def test_queries_target_the_active_batch_and_never_a_published_one():
    targets = SocialTargets(yc_batches=("Fall 2026",), speedrun_cohorts=("SR007",))

    for query in x_queries(targets):
        assert "S26" not in query          # Summer 2026 is fully published
    assert any("YC F26" in query for query in x_queries(targets))
    assert all(query.startswith("site:x.com") for query in x_queries(targets))

    linkedin = linkedin_queries(targets)
    assert any("YC F26" in q and "site:linkedin.com/posts" in q for q in linkedin)
    assert any("site:linkedin.com/company" in q for q in linkedin)
    assert any("Speedrun SR007" in q for q in linkedin)


def test_claim_queries_survive_without_any_batch_token():
    """Plenty of founders post "we got into YC" and name no batch at all."""
    targets = SocialTargets(yc_batches=("Fall 2026",))
    assert any("we got into YC" in query for query in x_queries(targets))
    assert any("accepted into Y Combinator" in q for q in linkedin_queries(targets))


def test_retargets_itself_when_a_new_batch_opens():
    before = x_queries(SocialTargets(yc_batches=("Summer 2026",)))
    after = x_queries(SocialTargets(yc_batches=("Fall 2026",)))
    assert before != after
    assert any("YC S26" in q for q in before)
    assert any("YC F26" in q for q in after)


def test_targets_round_trip_through_the_snapshot(tmp_path):
    db = Database(str(tmp_path / "targets.db"))
    db.record_snapshot("yc_directory", 6199, active_batches=["Fall 2026", "Summer 2026"])
    db.record_snapshot("speedrun", 261, active_batches=["SR007"])

    targets = SocialTargets.from_db(db)
    assert targets.yc_batches == ("Fall 2026", "Summer 2026")
    assert targets.speedrun_cohorts == ("SR007",)
    assert not targets.is_empty


@pytest.mark.parametrize("source_cls", [XSource, LinkedInSource])
@pytest.mark.anyio
async def test_social_sources_refuse_to_hunt_without_a_snapshot(source_cls, monkeypatch):
    """No official snapshot means no adjudicator, so a "not listed" claim would
    be unsupportable. Waiting is the correct behaviour, and it is not a fault."""
    # Credentials present so the guard under test is the one that fires.
    monkeypatch.setattr(
        "app.sources.social.settings",
        replace(app_settings, x_bearer_token="token", serper_api_key="key"),
    )

    with pytest.raises(RuntimeError, match="^waiting:"):
        await source_cls().collect()


def test_empty_targets_are_reported_as_empty():
    assert SocialTargets().is_empty
    assert SocialTargets(yc_batches=("Unspecified",)).is_empty
    assert not SocialTargets(yc_batches=("Fall 2026",)).is_empty


def test_linkedin_uses_one_query_per_batch():
    """The provider caps a free-tier response at 10 results.

    Folding two batches into one OR group halves the coverage of each, and the
    older fully-published batch wins the ranking -- exactly the wrong half to keep.
    """
    queries = linkedin_queries(
        SocialTargets(yc_batches=("Fall 2026", "Summer 2026"), speedrun_cohorts=("SR007", "SR006"))
    )
    post_queries = [q for q in queries if "site:linkedin.com/posts" in q]
    assert any("YC F26" in q and "YC S26" not in q for q in post_queries)
    assert any("YC S26" in q and "YC F26" not in q for q in post_queries)
    assert any("SR007" in q and "SR006" not in q for q in post_queries)
