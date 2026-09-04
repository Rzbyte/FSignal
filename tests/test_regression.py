from dataclasses import replace
from urllib.parse import unquote

import httpx
import pytest

from app.config import settings as app_settings
from app.sources.official import YCDirectorySource
from app.sources.social import LinkedInSource
from app.targeting import SocialTargets


class _MockResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self._json = json_data or {}
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(str(self.status_code), request=None, response=self)


_ALGOLIA_HTML = 'window.AlgoliaOpts={"app":"ABC","key":"k123"};'


def _hit(slug, name, batch, launched_at):
    """An Algolia hit shaped like the ones the live directory actually returns."""
    return {
        "slug": slug,
        "name": name,
        "batch": batch,
        "one_liner": f"{name} does things",
        "website": f"https://{slug}.example",
        "launched_at": launched_at,
        "_highlightResult": {"bulk": "x" * 50},
    }


def _install_yc_mocks(monkeypatch, batch_facets, batch_hits, recent_hits=None, nb_hits=None):
    """Route Algolia POSTs by their params: facet query, batch slice, or recent window."""
    calls = {"facet": 0, "slices": [], "recent": 0}

    async def mock_get(*args, **kwargs):
        return _MockResponse(text=_ALGOLIA_HTML)

    async def mock_post(*args, **kwargs):
        params = kwargs["json"]["requests"][0]["params"]
        if "facets=" in params:
            calls["facet"] += 1
            total = nb_hits if nb_hits is not None else sum(batch_facets.values())
            return _MockResponse(
                {"results": [{"facets": {"batch": batch_facets}, "nbHits": total}]}
            )
        if "filters=" in params:
            raw = unquote(params.split("filters=", 1)[1])
            batch = raw.removeprefix('batch:"').removesuffix('"')
            calls["slices"].append(batch)
            return _MockResponse({"results": [{"hits": batch_hits.get(batch, [])}]})
        calls["recent"] += 1
        return _MockResponse({"results": [{"hits": recent_hits or []}]})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    return calls


@pytest.mark.anyio
async def test_yc_full_crawl_slices_every_batch(monkeypatch):
    """A single Algolia query caps at 1000 hits, so completeness needs batch slicing."""
    facets = {"Fall 2026": 2, "Summer 2026": 1}
    hits = {
        "Fall 2026": [_hit("orca", "Orca Aerospace", "Fall 2026", 1_756_000_000),
                      _hit("nodus-compute", "Nodus Compute", "Fall 2026", 1_755_900_000)],
        "Summer 2026": [_hit("ultrasonium", "Ultrasonium", "Summer 2026", 1_750_000_000)],
    }
    calls = _install_yc_mocks(monkeypatch, facets, hits)

    source = YCDirectorySource()
    companies = await source.collect()

    assert calls["facet"] == 1
    assert sorted(calls["slices"]) == ["Fall 2026", "Summer 2026"]
    assert len(companies) == 3
    assert source.last_mode == "full"
    assert source.index_used == "YCCompany_By_Launch_Date_production"

    orca = next(c for c in companies if c.external_id == "orca")
    assert orca.name == "Orca Aerospace"
    assert orca.batch == "Fall 2026"
    assert orca.url == "https://www.ycombinator.com/companies/orca"
    # Bulk Algolia fields are dropped before the row is persisted.
    assert "_highlightResult" not in orca.raw
    assert orca.raw["launched_at"] == 1_756_000_000


@pytest.mark.anyio
async def test_yc_active_batches_ranked_by_launch_date(monkeypatch):
    """Active batches come from the data, never from a hardcoded batch string."""
    facets = {"Fall 2026": 1, "Summer 2026": 1, "Winter 2016": 1}
    hits = {
        "Fall 2026": [_hit("orca", "Orca Aerospace", "Fall 2026", 1_756_000_000)],
        "Summer 2026": [_hit("ultrasonium", "Ultrasonium", "Summer 2026", 1_750_000_000)],
        "Winter 2016": [_hit("oldco", "OldCo", "Winter 2016", 1_450_000_000)],
    }
    _install_yc_mocks(monkeypatch, facets, hits)

    source = YCDirectorySource(active_batch_count=2)
    await source.collect()

    assert source.active_batches == ["Fall 2026", "Summer 2026"]


@pytest.mark.anyio
async def test_yc_hot_refresh_is_a_single_recent_query(monkeypatch):
    """Between full crawls one recent-window query is enough to catch new listings."""
    facets = {"Fall 2026": 1}
    hits = {"Fall 2026": [_hit("orca", "Orca Aerospace", "Fall 2026", 1_756_000_000)]}
    recent = [_hit("newco", "NewCo", "Fall 2026", 1_756_500_000)]
    calls = _install_yc_mocks(monkeypatch, facets, hits, recent_hits=recent)

    source = YCDirectorySource(full_crawl_interval_minutes=10_000)
    await source.collect()
    assert source.last_mode == "full"

    companies = await source.collect()
    assert source.last_mode == "hot"
    assert calls["facet"] == 1          # not re-enumerated
    assert calls["recent"] == 1
    assert [c.external_id for c in companies] == ["newco"]


@pytest.mark.anyio
async def test_yc_facet_drift_raises_instead_of_shipping_partial_snapshot(monkeypatch):
    """A partial snapshot would silently poison every early-detection verdict."""
    facets = {"Fall 2026": 2}
    hits = {"Fall 2026": [_hit("orca", "Orca Aerospace", "Fall 2026", 1_756_000_000),
                          _hit("nodus-compute", "Nodus Compute", "Fall 2026", 1_755_900_000)]}
    _install_yc_mocks(monkeypatch, facets, hits, nb_hits=6199)

    source = YCDirectorySource()
    with pytest.raises(RuntimeError, match="directory shape changed"):
        await source.collect()


@pytest.mark.anyio
async def test_yc_short_batch_slice_raises(monkeypatch):
    """If a slice returns fewer companies than the facet promised, do not proceed."""
    facets = {"Fall 2026": 2}
    hits = {"Fall 2026": [_hit("orca", "Orca Aerospace", "Fall 2026", 1_756_000_000)]}
    _install_yc_mocks(monkeypatch, facets, hits)

    source = YCDirectorySource()
    with pytest.raises(RuntimeError, match="returned 1 of 2"):
        await source.collect()


@pytest.mark.anyio
async def test_yc_zero_results(monkeypatch):
    _install_yc_mocks(monkeypatch, {}, {})

    source = YCDirectorySource()
    with pytest.raises(RuntimeError, match="no batch facet"):
        await source.collect()


@pytest.mark.anyio
async def test_linkedin_serper_request(monkeypatch):
    class MockResponse:
        def __init__(self, text, json_data, status_code):
            self.text = text
            self._json = json_data
            self.status_code = status_code

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("400 Error", request=None, response=self)

    async def mock_post(*args, **kwargs):
        json_payload = kwargs.get("json", {})
        assert "q" in json_payload
        assert json_payload["num"] == 10
        assert json_payload["tbs"].startswith("qdr:")
        
        return MockResponse("", {
            "organic": [{
                "title": "Test Post",
                "link": "https://www.linkedin.com/posts/test",
                "snippet": "Test snippet YC S26"
            }]
        }, 200)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    def mock_enrich(signal):
        signal.confidence = 95
        signal.company_name = "Test Company"
    monkeypatch.setattr("app.sources.social.enrich_signal", mock_enrich)

    monkeypatch.setattr(
        "app.sources.social.settings", replace(app_settings, serper_api_key="test-key")
    )
    source = LinkedInSource(SocialTargets(yc_batches=("Fall 2026",)))
    signals = await source.collect()
    assert len(signals) == 1
    assert signals[0].url == "https://www.linkedin.com/posts/test"
    assert signals[0].confidence > 0 # Normalization worked

@pytest.mark.anyio
async def test_linkedin_serper_400_retains_detail(monkeypatch):
    class MockResponse:
        def __init__(self, text, json_data, status_code):
            self.text = text
            self._json = json_data
            self.status_code = status_code

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        return MockResponse("Bad Request Detail String", {}, 400)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    def mock_enrich(signal):
        signal.confidence = 95
        signal.company_name = "Test Company"
    monkeypatch.setattr("app.sources.social.enrich_signal", mock_enrich)

    monkeypatch.setattr(
        "app.sources.social.settings", replace(app_settings, serper_api_key="test-key")
    )
    source = LinkedInSource(SocialTargets(yc_batches=("Fall 2026",)))
    with pytest.raises(RuntimeError, match="Bad Request Detail String"):
        await source.collect()

@pytest.mark.anyio
async def test_speedrun_official_parser():
    from app.sources.official import SpeedrunSource
    data = {
        "results": [
            {
                "slug": "simula",
                "name": "Simula",
                "cohort": "SR007",
                "description": "Simula description",
                "website_url": "https://simula.com"
            }
        ]
    }
    companies = SpeedrunSource.parse_primary_api(data)
    assert len(companies) == 1
    assert companies[0].name == "Simula"
    assert companies[0].batch == "SR007"
    assert companies[0].url == "https://speedrun.a16z.com/companies/simula"

@pytest.mark.anyio
async def test_speedrun_zero_results(monkeypatch):
    from app.sources.official import SpeedrunSource
    class MockResponse:
        def __init__(self, text, json_data, status_code):
            self.text = text
            self._json = json_data
            self.status_code = status_code
        def json(self): return self._json
        def raise_for_status(self): pass

    async def mock_get(*args, **kwargs):
        return MockResponse("", {"results": []}, 200)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    source = SpeedrunSource(fallback_url=None) # no fallback
    with pytest.raises(RuntimeError, match="Zero companies returned"):
        await source.collect()

@pytest.mark.anyio
async def test_linkedin_post_without_a_company_is_not_actionable():
    """This used to yield a company literally named "Unknown (Deterministic)",
    which is truthy and so defeated the identity check downstream."""
    payload = {
        "organic": [{
            "title": "We are joining YC S26!",
            "snippet": "It is official, we got into Y Combinator",
            "link": "https://www.linkedin.com/posts/test1234"
        }]
    }
    signals = LinkedInSource.parse_response(payload)
    assert len(signals) == 1
    assert signals[0].company_name is None
    assert not signals[0].extraction.is_usable
    assert signals[0].extraction.reason == "no_company_identity"
