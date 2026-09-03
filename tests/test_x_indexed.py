"""The X source has two paths, and the second one must not lie about the first.

X's own recent search is a paid product. An account without a plan gets `402`,
and for most of this project's life that meant the X source produced nothing at
all -- implemented, tested, and permanently dark. The indexed fallback runs the
same vocabulary against publicly indexed X URLs so the source actually reports.

Two properties matter more than coverage here:

* a post found through either path is *the same post*, so it can only alert once;
* an indexed result is never presented as a native one.

Every fixture in this file is a real captured provider payload
(`tests/fixtures/x_indexed_payload.json`), not hand-written JSON.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings as app_settings
from app.sources.social import XIndexedSource, XSource
from app.targeting import SocialTargets, x_indexed_queries

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "x_indexed_payload.json").read_text()
)

TARGETS = SocialTargets(yc_batches=("Fall 2026",), speedrun_cohorts=("SR007",))


def _credentials(monkeypatch, **overrides) -> None:
    """Swap the module's settings object.

    `Settings` is frozen, so a credential is injected by replacing the whole
    object rather than mutating a field -- the same approach `test_targeting`
    uses.
    """
    monkeypatch.setattr(
        "app.sources.social.settings", replace(app_settings, **overrides)
    )


# --------------------------------------------------------------------------- #
# Query vocabulary                                                             #
# --------------------------------------------------------------------------- #


def test_queries_are_scoped_to_x_and_carry_the_live_batch():
    queries = x_indexed_queries(TARGETS)
    assert all(query.startswith("site:x.com (") for query in queries)
    assert any('"YC F26"' in query for query in queries)
    assert any('"Speedrun SR007"' in query for query in queries)


def test_queries_never_carry_platform_operators():
    """``-is:retweet`` is an X operator. A search engine reads it as a word."""
    assert all("-is:retweet" not in query for query in x_indexed_queries(TARGETS))


def test_claim_queries_survive_an_empty_directory_snapshot():
    """Plenty of founders post "we got into YC" and never name the batch."""
    queries = x_indexed_queries(SocialTargets())
    assert any('"we got into YC"' in query for query in queries)
    assert any('"joining a16z Speedrun"' in query for query in queries)


def test_each_batch_gets_its_own_query():
    """A combined OR group spends its ten results on the older batch."""
    targets = SocialTargets(yc_batches=("Fall 2026", "Summer 2026"))
    queries = [query for query in x_indexed_queries(targets) if "YC F26" in query]
    assert len(queries) == 1
    assert "YC S26" not in queries[0]


# --------------------------------------------------------------------------- #
# Parsing a real provider payload                                              #
# --------------------------------------------------------------------------- #


def test_every_result_in_the_captured_payload_parses():
    signals = XIndexedSource.parse_response(FIXTURE)
    assert len(signals) == len(FIXTURE["organic"])


def test_the_post_id_is_the_external_id():
    """Not a hash of the URL.

    The native collector keys on the same X post id, so a post found both ways
    collapses to one row under UNIQUE(source, external_id) rather than alerting
    twice. Hashing the link -- which is what the LinkedIn adapter does, because
    LinkedIn has no comparable id -- would break that.
    """
    signals = {signal.external_id: signal for signal in
               XIndexedSource.parse_response(FIXTURE)}
    assert "2085432378822963431" in signals
    assert all(signal.external_id.isdigit() for signal in signals.values())


def test_the_handle_comes_from_the_url_path():
    signals = {s.external_id: s for s in XIndexedSource.parse_response(FIXTURE)}
    assert signals["2085432378822963431"].author_handle == "herdrdev"


def test_the_display_name_comes_from_the_result_title():
    """`Tsenta (YC S26) on X` and `Alex Danilowicz on X: "..."` both resolve."""
    signals = {s.external_id: s for s in XIndexedSource.parse_response(FIXTURE)}
    assert signals["2085459177653272962"].author_name == "Tsenta (YC S26)"
    assert signals["2085139536955220365"].author_name == "Alex Danilowicz"


def test_a_localised_title_still_yields_the_founder():
    """The provider serves whatever locale it ranked the result in.

    A live run returned `Adalat AI (YC F26) على X: "5/ With YC behind us..."`.
    Matching the literal word "on" would have thrown away the founder on exactly
    the signal that became an EARLY alert.
    """
    signals = XIndexedSource.parse_response(
        {
            "organic": [
                {
                    "link": "https://x.com/Adalat_AI/status/2090071721856712924",
                    "title": 'Adalat AI (YC F26) على X: "5/ With YC behind us, we',
                }
            ]
        }
    )
    assert signals[0].author_name == "Adalat AI (YC F26)"


def test_a_title_that_is_only_post_text_yields_no_founder():
    """Better an honest blank than a fragment of the announcement as a name."""
    signals = XIndexedSource.parse_response(
        {
            "organic": [
                {
                    "link": "https://x.com/Adalat_AI/status/2090071662784086176",
                    "title": '"1/ Adalat AI is now backed by Y Combinator. We are',
                }
            ]
        }
    )
    assert signals[0].author_name is None
    # The handle is in the URL, so the founder is still reachable.
    assert signals[0].author_handle == "Adalat_AI"


@pytest.mark.parametrize(
    "link,expected_id",
    [
        ("https://x.com/founder/status/123", "123"),
        # A photo permalink is the same post.
        ("https://x.com/founder/status/123/photo/1", "123"),
        # Language and mobile variants are the same post.
        ("https://x.com/founder/status/123?lang=ar", "123"),
        ("https://mobile.x.com/founder/status/123", "123"),
        ("https://www.x.com/founder/status/123", "123"),
    ],
)
def test_url_variants_collapse_to_one_post(link, expected_id):
    signals = XIndexedSource.parse_response({"organic": [{"link": link}]})
    assert [signal.external_id for signal in signals] == [expected_id]
    assert signals[0].url == f"https://x.com/founder/status/{expected_id}"


@pytest.mark.parametrize(
    "link",
    [
        # The provider really does return all of these.
        "https://x.com/devonmeadows/with_replies?lang=bn",
        "https://x.com/zhang_baoqing/reposts",
        "https://x.com/RamyaSreeB?lang=bg",
        "https://twitter.com/founder/status/123",  # not the canonical host
        "https://www.linkedin.com/posts/someone_activity-123",
        "",
    ],
)
def test_non_post_urls_are_dropped(link):
    assert XIndexedSource.parse_response({"organic": [{"link": link}]}) == []


def test_the_i_routing_segment_is_not_a_handle():
    """``x.com/i/status/123`` is a real post URL; ``i`` is not anybody."""
    signals = XIndexedSource.parse_response(
        {"organic": [{"link": "https://x.com/i/status/123"}]}
    )
    assert signals[0].author_handle is None
    assert signals[0].external_id == "123"


def test_the_collection_path_is_recorded_on_every_signal():
    signals = XIndexedSource.parse_response(FIXTURE)
    assert {signal.collection_mode for signal in signals} == {"indexed_fallback"}


# --------------------------------------------------------------------------- #
# Delegation                                                                   #
# --------------------------------------------------------------------------- #


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("raise_for_status should not be reached in these tests")


class _Client:
    """Stands in for httpx.AsyncClient for both the native and indexed calls."""

    def __init__(self, *, get=None, post=None):
        self._get = get
        self._post = post
        self.posted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return self._get

    async def post(self, url, json=None):
        self.posted.append(json)
        return self._post


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(
        "app.sources.social.httpx.AsyncClient", lambda **kwargs: client
    )


@pytest.mark.anyio
async def test_a_depleted_account_falls_back_instead_of_going_dark(monkeypatch):
    _credentials(monkeypatch, x_bearer_token="native-token", serper_api_key="key")
    client = _Client(
        get=_Response(402, text="credits depleted"),
        post=_Response(200, FIXTURE),
    )
    _patch_client(monkeypatch, client)

    source = XSource()
    source.targets = TARGETS
    signals = await source.collect()

    assert source.last_mode == "indexed_fallback"
    assert len(signals) == len(FIXTURE["organic"])
    assert all(query["q"].startswith("site:x.com") for query in client.posted)


@pytest.mark.anyio
async def test_an_absent_bearer_token_falls_back(monkeypatch):
    _credentials(monkeypatch, x_bearer_token="", serper_api_key="key")
    _patch_client(monkeypatch, _Client(post=_Response(200, FIXTURE)))

    source = XSource()
    source.targets = TARGETS
    assert await source.collect()
    assert source.last_mode == "indexed_fallback"


@pytest.mark.anyio
async def test_a_working_native_call_is_never_second_guessed(monkeypatch):
    _credentials(monkeypatch, x_bearer_token="native-token", serper_api_key="key")
    native = {
        "data": [{"id": "999", "text": "we got into YC F26", "author_id": "7"}],
        "includes": {"users": [{"id": "7", "name": "Jane", "username": "janef"}]},
    }
    client = _Client(get=_Response(200, native), post=_Response(200, FIXTURE))
    _patch_client(monkeypatch, client)

    source = XSource()
    source.targets = TARGETS
    signals = await source.collect()

    assert source.last_mode == "native"
    assert client.posted == []
    assert [signal.collection_mode for signal in signals] == ["native"]


@pytest.mark.anyio
async def test_without_a_search_key_the_real_reason_still_surfaces(monkeypatch):
    """Falling back to a provider we cannot reach would hide the actual fault."""
    _credentials(monkeypatch, x_bearer_token="native-token", serper_api_key="")
    _patch_client(monkeypatch, _Client(get=_Response(402, text="credits depleted")))

    source = XSource()
    source.targets = TARGETS
    with pytest.raises(RuntimeError, match="billing_blocked"):
        await source.collect()


@pytest.mark.anyio
async def test_an_http_fault_is_not_papered_over_by_the_fallback(monkeypatch):
    """A 500 from X is a fault to report, not a reason to answer differently."""
    _credentials(monkeypatch, x_bearer_token="native-token", serper_api_key="key")

    class _Failing(_Client):
        async def get(self, url, params=None):
            raise RuntimeError("boom")

    _patch_client(monkeypatch, _Failing(post=_Response(200, FIXTURE)))

    source = XSource()
    source.targets = TARGETS
    with pytest.raises(RuntimeError, match="boom"):
        await source.collect()


@pytest.mark.anyio
async def test_no_official_snapshot_means_wait_not_fall_back(monkeypatch):
    """Hunting with no known batch is worse than not hunting."""
    _credentials(monkeypatch, x_bearer_token="native-token", serper_api_key="key")
    _patch_client(monkeypatch, _Client(post=_Response(200, FIXTURE)))

    source = XSource()
    source.targets = SocialTargets()
    with pytest.raises(RuntimeError, match="waiting:"):
        await source.collect()


def test_assigning_targets_reaches_the_fallback():
    """The scanner assigns `source.targets` directly before each run."""
    source = XSource()
    source.targets = TARGETS
    assert source.fallback.targets is TARGETS
