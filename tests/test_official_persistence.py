"""Official-company persistence: one write path, and safe retirement.

Two failures live here. The first is quiet: a company that leaves the directory
goes on matching forever, and every match reads downstream as "already listed,
therefore not early" -- one stale row suppressing a real signal for good.

The second is loud, and worse. Retiring rows on a fetch that only *looked*
complete would empty the corpus that every EARLY verdict is checked against.
So the tests that matter most here are the ones asserting that nothing was
pruned.
"""

import asyncio

import pytest

from app.db import Database
from app.engine import RadarEngine
from app.models import Company
from app.scanner import COMPLETE_SNAPSHOT_MODES


class Silent:
    """The engine needs a notifier; these tests are about persistence."""

    async def send_ghost(self, signal):
        return {"ok": True, "ts": "1"}

    async def send_corroboration(self, signal, thread_ts):
        return {"ok": True}

    async def send_confirmed(self, signal, company):
        return {"ok": True}

    async def send_official(self, company):
        return {"ok": True}


def company(external_id, name, *, batch="Fall 2026", listed_at="2026-08-01T00:00:00+00:00"):
    return Company(
        source="yc_directory",
        external_id=external_id,
        name=name,
        batch=batch,
        url=f"https://www.ycombinator.com/companies/{external_id}",
        description=f"{name} does things.",
        listed_at=listed_at,
    )


def radar(tmp_path, name):
    database = Database(str(tmp_path / name))
    return database, RadarEngine(database, Silent())


def names(rows):
    return {row["name"] for row in rows}


# --- one write path ----------------------------------------------------------


def test_single_and_bulk_upserts_persist_the_same_fields(tmp_path):
    """The single-row path once omitted listed_at, so a row written through it
    scored its lead time against our polling clock instead of the directory's."""
    database = Database(str(tmp_path / "parity.db"))

    database.upsert_company(company("solo", "Solo Co"))
    database.upsert_companies([company("bulk", "Bulk Co")])

    stored = {row["external_id"]: row for row in database.list_official()}
    solo, bulk = stored["solo"], stored["bulk"]

    for field in ("name", "batch", "url", "description", "listed_at", "normalized_name",
                  "source", "stale_at"):
        assert (solo[field] is None) == (bulk[field] is None), field
    assert solo["listed_at"] == bulk["listed_at"] == "2026-08-01T00:00:00+00:00"


def test_upsert_reports_new_then_existing(tmp_path):
    database = Database(str(tmp_path / "isnew.db"))
    assert database.upsert_company(company("a", "A Co"))[1] is True
    assert database.upsert_company(company("a", "A Co renamed"))[1] is False
    assert names(database.list_official()) == {"A Co renamed"}


# --- retirement, when the directory really has stopped listing something -----


def test_a_completed_full_snapshot_retires_what_it_no_longer_lists(tmp_path):
    database, engine = radar(tmp_path, "prune.db")
    asyncio.run(
        engine.ingest_official(
            [company("keep", "Keep Co"), company("gone", "Gone Co")],
            complete_snapshot=True,
        )
    )
    assert names(database.list_official()) == {"Keep Co", "Gone Co"}

    asyncio.run(
        engine.ingest_official([company("keep", "Keep Co")], complete_snapshot=True)
    )

    assert names(database.list_official()) == {"Keep Co"}
    assert database.count_official("yc_directory") == 1


def test_a_retired_row_is_marked_not_deleted(tmp_path):
    """A confirmed signal points at a row by id; its timeline has to keep
    resolving whatever the directory did afterwards."""
    database, engine = radar(tmp_path, "marked.db")
    asyncio.run(
        engine.ingest_official(
            [company("keep", "Keep Co"), company("gone", "Gone Co")],
            complete_snapshot=True,
        )
    )
    gone_id = next(
        row["id"] for row in database.list_official() if row["external_id"] == "gone"
    )

    asyncio.run(
        engine.ingest_official([company("keep", "Keep Co")], complete_snapshot=True)
    )

    still_there = database.get_official(gone_id)
    assert still_there is not None
    assert still_there["stale_at"] is not None


def test_a_returning_company_becomes_live_again(tmp_path):
    database, engine = radar(tmp_path, "return.db")
    both = [company("keep", "Keep Co"), company("gone", "Gone Co")]

    asyncio.run(engine.ingest_official(both, complete_snapshot=True))
    asyncio.run(
        engine.ingest_official([company("keep", "Keep Co")], complete_snapshot=True)
    )
    assert names(database.list_official()) == {"Keep Co"}

    asyncio.run(engine.ingest_official(both, complete_snapshot=True))
    assert names(database.list_official()) == {"Keep Co", "Gone Co"}


# --- and the far more dangerous direction ------------------------------------


def test_a_partial_snapshot_never_retires_anything(tmp_path):
    """YC's recent window returns the newest listings only. Treating what it did
    not return as withdrawn would retire the entire directory."""
    database, engine = radar(tmp_path, "partial.db")
    asyncio.run(
        engine.ingest_official(
            [company(str(i), f"Co {i}") for i in range(10)], complete_snapshot=True
        )
    )

    asyncio.run(engine.ingest_official([company("0", "Co 0")], complete_snapshot=False))

    assert database.count_official("yc_directory") == 10


def test_a_suspiciously_small_full_snapshot_is_refused(tmp_path):
    """The guard for a crawl that finished cleanly and still returned garbage.
    Re-listing is not something a later scan can do for rows that were correct."""
    database, engine = radar(tmp_path, "shrunk.db")
    asyncio.run(
        engine.ingest_official(
            [company(str(i), f"Co {i}") for i in range(100)], complete_snapshot=True
        )
    )

    # A "successful" crawl returning 5% of the directory.
    asyncio.run(
        engine.ingest_official(
            [company(str(i), f"Co {i}") for i in range(5)], complete_snapshot=True
        )
    )

    assert database.count_official("yc_directory") == 100


def test_an_empty_snapshot_never_empties_the_corpus(tmp_path):
    database, engine = radar(tmp_path, "empty.db")
    asyncio.run(
        engine.ingest_official([company("a", "A Co")], complete_snapshot=True)
    )
    assert database.mark_absent_as_stale("yc_directory", []) == 0
    assert database.count_official("yc_directory") == 1


def test_retirement_is_scoped_to_the_source_that_reported(tmp_path):
    """A complete YC crawl says nothing about what Speedrun lists."""
    database = Database(str(tmp_path / "scoped.db"))
    database.upsert_companies(
        [
            company("yc-1", "YC Co"),
            Company(
                source="speedrun",
                external_id="sr-1",
                name="SR Co",
                url="https://speedrun.a16z.com/companies/sr-1",
                batch="SR007",
            ),
        ]
    )

    database.mark_absent_as_stale("yc_directory", ["yc-1"])

    assert names(database.list_official()) == {"YC Co", "SR Co"}


# --- the reason any of this matters ------------------------------------------


def test_a_retired_row_stops_suppressing_a_later_early_signal(tmp_path):
    """The quiet failure, end to end.

    While "Nodus" is listed, a founder post about it is correctly not early.
    Once the directory drops it, the same post must be able to alert again --
    otherwise one withdrawn row silences that company permanently.
    """
    from app.matcher import match_official

    database, engine = radar(tmp_path, "suppress.db")
    asyncio.run(
        engine.ingest_official(
            [company("nodus", "Nodus"), company("other", "Other Co")],
            complete_snapshot=True,
        )
    )
    assert match_official("Nodus", None, database.list_official(), "F26")

    asyncio.run(
        engine.ingest_official([company("other", "Other Co")], complete_snapshot=True)
    )

    assert not match_official("Nodus", None, database.list_official(), "F26")


@pytest.mark.parametrize("mode", sorted(COMPLETE_SNAPSHOT_MODES))
def test_only_whole_directory_modes_are_treated_as_complete(mode):
    assert mode in COMPLETE_SNAPSHOT_MODES


@pytest.mark.parametrize("mode", ["hot", "fallback", None, "indexed_fallback"])
def test_partial_modes_are_not_treated_as_complete(mode):
    assert mode not in COMPLETE_SNAPSHOT_MODES
