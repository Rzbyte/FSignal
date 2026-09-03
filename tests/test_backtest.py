"""The lead-time backtest has to be wrong in the safe direction.

It exists to answer the one question the whole product is judged on -- how much
earlier than the directory -- from data that is already public. That makes it
the easiest artifact in the repository to accidentally flatter, so the two
guards below matter more than its coverage:

* a company page has no announcement date, and using its indexed date as one
  inflated the first run's headline to 200 days;
* an unparseable date is dropped rather than guessed at.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "backtest", Path(__file__).resolve().parents[1] / "scripts" / "backtest_lead_time.py"
)
backtest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backtest)

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Aug 19, 2026", datetime(2026, 8, 19, tzinfo=timezone.utc)),
        ("Sep 2, 2026", datetime(2026, 9, 2, tzinfo=timezone.utc)),
        ("3 days ago", datetime(2026, 8, 31, tzinfo=timezone.utc)),
    ],
)
def test_post_dates_the_index_actually_returns(value, expected):
    assert backtest.parse_post_date(value, NOW) == expected


@pytest.mark.parametrize("value", ["", None, "garbage", "sometime last spring"])
def test_an_unreadable_date_is_dropped_not_guessed(value):
    """A guessed date becomes a fabricated lead time."""
    assert backtest.parse_post_date(value, NOW) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/rightnowai_co/status/2041480726181220852",
        "https://www.linkedin.com/posts/alymoursy_super-proud-activity-7494963581126586368-IXTX",
    ],
)
def test_a_dated_post_is_measurable(url):
    assert backtest._DATED_POST.match(url)


@pytest.mark.parametrize(
    "url",
    [
        # The first run measured a 200-day lead off this shape. The date a search
        # index reports for a company page is when the page was created or last
        # crawled -- not when anybody announced anything.
        "https://www.linkedin.com/company/pingdream",
        "https://www.linkedin.com/company/trystudioai",
        "https://x.com/rightnowai_co",
        "https://x.com/someone/with_replies",
    ],
)
def test_a_company_page_is_never_measured_as_an_announcement(url):
    assert backtest._DATED_POST.match(url) is None
