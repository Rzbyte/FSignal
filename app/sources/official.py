"""Official accelerator directory collectors.

The directory adapters are intentionally isolated from the detection engine so a
future canonical URL or additional accelerator can be swapped in without touching
state, matching, or Slack delivery logic.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..models import Company


def _node_context(node, max_hops: int = 4) -> str:
    """Return nearby human-visible text for metadata extraction."""
    current = node
    best = " ".join(node.stripped_strings)
    for _ in range(max_hops):
        current = getattr(current, "parent", None)
        if current is None:
            break
        text = " ".join(current.stripped_strings)
        if text and len(text) <= 2500:
            best = text
    return best


#: The public directory page hands its own JavaScript a restricted Algolia key.
ALGOLIA_OPTS_PATTERN = re.compile(r"window\.AlgoliaOpts\s*=\s*(\{.*?\});")

#: Algolia refuses to paginate past this many hits for one query
#: (``paginationLimitedTo``), which is why a full crawl has to slice by batch.
ALGOLIA_MAX_HITS_PER_QUERY = 1000

_FACET_PARAMS = "query=&hitsPerPage=0&facets=%5B%22batch%22%5D&maxValuesPerFacet=1000"
_RECENT_PARAMS = f"query=&hitsPerPage={ALGOLIA_MAX_HITS_PER_QUERY}"

#: How many of the ~50 per-batch slice queries to run at once.
_FULL_CRAWL_CONCURRENCY = 5

#: Bulky Algolia fields with no monitoring value. Six thousand copies of the
#: highlight blocks and long descriptions would dominate the SQLite file.
_RAW_DROP_FIELDS = frozenset(
    {
        "_highlightResult",
        "long_description",
        "question_answers",
        "app_answers",
        "tags_highlighted",
    }
)


class YCDirectorySource:
    """Monitor the official YC company directory.

    ycombinator.com/companies is a client-side Algolia application: the page
    ships a public, index-restricted search key in ``window.AlgoliaOpts`` and the
    browser queries Algolia directly. This adapter uses that same client-facing
    data path rather than scraping rendered HTML.

    Retrieval has two modes, because a launch monitor needs two different things:

    ``full``
        Every company in the directory. This snapshot is the adjudicator for
        early detection -- an "not yet listed" verdict is only as trustworthy as
        the corpus it was checked against. A single Algolia query cannot return
        more than 1000 hits, so the crawl enumerates the ``batch`` facet and
        pulls one filtered slice per batch. No individual YC batch is close to
        the cap.

    ``hot``
        Only the most recently listed companies. One query, a few seconds, and it
        is what actually catches a new listing between full crawls.

    The index is the launch-date replica, so the newest listings sort first and
    the question "which batches are currently filling?" is answered by the data
    instead of a hardcoded string that goes stale every few months.
    """

    name = "yc_directory"

    def __init__(
        self,
        url: str | None = None,
        index_name: str | None = None,
        fallback_index_name: str | None = None,
        active_batch_count: int | None = None,
        full_crawl_interval_minutes: float | None = None,
    ):
        self.url = url or settings.yc_directory_url
        self.index_name = index_name or settings.yc_index_name
        self.fallback_index_name = fallback_index_name or settings.yc_fallback_index_name
        self.active_batch_count = active_batch_count or settings.active_batch_count
        minutes = (
            full_crawl_interval_minutes
            if full_crawl_interval_minutes is not None
            else settings.yc_full_crawl_interval_minutes
        )
        self._full_crawl_interval = timedelta(minutes=minutes)
        self._last_full_crawl: datetime | None = None

        # Read by the scanner (snapshot bookkeeping) and by social targeting.
        self.last_mode: str | None = None
        self.index_used: str | None = None
        self.active_batches: list[str] = []

    async def collect(self) -> list[Company]:
        now = datetime.now(timezone.utc)
        full_crawl = self._is_full_crawl_due(now)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        ) as client:
            app_id, api_key = await self._bootstrap(client)

            index = self.index_name
            try:
                companies = await self._retrieve(client, app_id, api_key, index, full_crawl)
            except RuntimeError:
                if index == self.fallback_index_name:
                    raise
                # The launch-date replica is the right index for this product but
                # it is a YC implementation detail. Degrade rather than go blind.
                index = self.fallback_index_name
                companies = await self._retrieve(client, app_id, api_key, index, full_crawl)

        if not companies:
            raise RuntimeError("yc_directory: zero companies returned from YC Algolia.")

        if full_crawl:
            self._last_full_crawl = now
        self.last_mode = "full" if full_crawl else "hot"
        self.index_used = index
        self.active_batches = self._rank_active_batches(companies)
        return companies

    def _is_full_crawl_due(self, now: datetime) -> bool:
        """A fresh process always crawls fully first: the baseline depends on it."""
        return (
            self._last_full_crawl is None
            or now - self._last_full_crawl >= self._full_crawl_interval
        )

    async def _retrieve(
        self,
        client: httpx.AsyncClient,
        app_id: str,
        api_key: str,
        index: str,
        full_crawl: bool,
    ) -> list[Company]:
        if full_crawl:
            return await self._full_crawl(client, app_id, api_key, index)
        result = await self._query(client, app_id, api_key, index, _RECENT_PARAMS)
        return self.parse_algolia(result.get("hits", []))

    async def _bootstrap(self, client: httpx.AsyncClient) -> tuple[str, str]:
        """Read the public Algolia credentials the directory hands its own JS."""
        response = await client.get(self.url)
        response.raise_for_status()

        match = ALGOLIA_OPTS_PATTERN.search(response.text)
        if not match:
            raise RuntimeError(
                "yc_directory: could not locate AlgoliaOpts in the directory HTML."
            )
        try:
            opts = json.loads(match.group(1))
            return opts["app"], opts["key"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError(f"yc_directory: failed to parse AlgoliaOpts: {exc}") from exc

    @staticmethod
    async def _query(
        client: httpx.AsyncClient,
        app_id: str,
        api_key: str,
        index: str,
        params: str,
    ) -> dict:
        response = await client.post(
            f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/*/queries",
            headers={
                "x-algolia-application-id": app_id,
                "x-algolia-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={"requests": [{"indexName": index, "params": params}]},
        )
        response.raise_for_status()

        results = (response.json() or {}).get("results") or []
        if not results:
            raise RuntimeError(f"yc_directory: empty Algolia response for index {index!r}.")

        result = results[0]
        # Algolia reports per-index authorisation failures inside a 200 response.
        if "hits" not in result and "facets" not in result:
            raise RuntimeError(
                f"yc_directory: Algolia rejected index {index!r}: "
                f"{result.get('message', result)}"
            )
        return result

    async def _full_crawl(
        self,
        client: httpx.AsyncClient,
        app_id: str,
        api_key: str,
        index: str,
    ) -> list[Company]:
        facet_result = await self._query(client, app_id, api_key, index, _FACET_PARAMS)
        batch_counts = (facet_result.get("facets") or {}).get("batch") or {}
        expected = facet_result.get("nbHits", 0)

        if not batch_counts:
            raise RuntimeError(
                f"yc_directory: index {index!r} exposes no batch facet to slice on."
            )

        # If the facet stops accounting for the index, its shape changed under us.
        # A silently partial snapshot would poison every early-detection verdict,
        # so fail loudly instead of shipping one.
        facet_total = sum(batch_counts.values())
        if abs(facet_total - expected) > 5:
            raise RuntimeError(
                f"yc_directory: batch facet totals {facet_total} but the index reports "
                f"{expected} companies - directory shape changed."
            )

        semaphore = asyncio.Semaphore(_FULL_CRAWL_CONCURRENCY)

        async def slice_batch(batch: str) -> list[Company]:
            escaped = batch.replace("\\", "\\\\").replace('"', '\\"')
            filters = quote(f'batch:"{escaped}"')
            async with semaphore:
                result = await self._query(
                    client,
                    app_id,
                    api_key,
                    index,
                    f"query=&hitsPerPage={ALGOLIA_MAX_HITS_PER_QUERY}&filters={filters}",
                )
            hits = result.get("hits", [])
            if len(hits) < min(batch_counts[batch], ALGOLIA_MAX_HITS_PER_QUERY):
                raise RuntimeError(
                    f"yc_directory: batch {batch!r} returned {len(hits)} of "
                    f"{batch_counts[batch]} companies."
                )
            return self.parse_algolia(hits)

        companies: dict[str, Company] = {}
        for group in await asyncio.gather(*(slice_batch(b) for b in batch_counts)):
            for company in group:
                companies[company.external_id] = company

        if len(companies) < expected - 5:
            raise RuntimeError(
                f"yc_directory: crawl covered {len(companies)} of {expected} companies."
            )
        return list(companies.values())

    def _rank_active_batches(self, companies: list[Company]) -> list[str]:
        """Return the batches that are currently filling, newest first.

        Ranked by the most recent ``launched_at`` seen in each batch rather than
        by parsing batch labels, so this keeps working when YC renames, reorders,
        or adds cohorts.
        """
        newest: dict[str, int] = {}
        for company in companies:
            launched = (company.raw or {}).get("launched_at")
            if not company.batch or not isinstance(launched, (int, float)):
                continue
            if launched > newest.get(company.batch, 0):
                newest[company.batch] = int(launched)

        ranked = sorted(newest, key=lambda batch: newest[batch], reverse=True)
        return ranked[: self.active_batch_count]

    @classmethod
    def parse_algolia(cls, hits: list[dict]) -> list[Company]:
        companies: dict[str, Company] = {}
        for hit in hits:
            slug = hit.get("slug")
            if not slug:
                continue
            companies[slug] = Company(
                name=(hit.get("name") or "Unknown")[:120],
                source=cls.name,
                external_id=slug,
                url=f"https://www.ycombinator.com/companies/{slug}",
                batch=hit.get("batch", ""),
                description=hit.get("one_liner", ""),
                domain=hit.get("website", ""),
                raw={k: v for k, v in hit.items() if k not in _RAW_DROP_FIELDS},
            )
        return list(companies.values())


class SpeedrunSource:
    """Monitor the a16z Speedrun company directory through its own API.

    The task brief calls this a "YC Speedrun" page; the public, distinct Speedrun
    company directory is a16z Speedrun. `speedrun.a16z.com/companies` is a Next.js
    application that fetches `speedrun-api.a16z.com/api/companies/companies/`
    itself, so that endpoint is the canonical client-facing data path -- not a
    third-party mirror. It is configurable through SPEEDRUN_API_URL.

    The talent-network URL is resilience only. It is used if the canonical API
    fails, and runs are labelled `fallback` so no evidence taken from it can be
    mistaken for proof that canonical monitoring works.
    """

    name = "speedrun"

    def __init__(
        self,
        url: str | None = None,
        fallback_url: str | None = None,
        active_batch_count: int | None = None,
        api_url: str | None = None,
    ):
        self.url = url or settings.speedrun_url
        self.api_url = api_url or settings.speedrun_api_url
        self.fallback_url = fallback_url or settings.speedrun_fallback_url
        self.active_batch_count = active_batch_count or settings.active_batch_count

        # Mirrors YCDirectorySource so the scanner can treat both the same way.
        self.last_mode: str | None = None
        self.index_used: str | None = None
        self.active_batches: list[str] = []

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    async def collect(self) -> list[Company]:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        ) as client:
            try:
                api_url = self.api_url
                response = await client.get(api_url)
                response.raise_for_status()
                data = response.json()
                companies = self.parse_primary_api(data, base_url="https://speedrun.a16z.com/companies")
                if not companies:
                    raise RuntimeError("Zero companies returned")
                self.last_mode = "canonical"
                self.index_used = api_url
                self.active_batches = self._rank_active_cohorts(companies)
                return companies
            except Exception as e:
                if not self.fallback_url or self.fallback_url == self.url:
                    raise RuntimeError(f"speedrun: Official source failed ({e}) and no fallback configured.")

                fallback_html = await self._fetch(client, self.fallback_url)
                companies = self.parse_fallback(fallback_html, self.fallback_url)
                if not companies:
                    raise RuntimeError(f"speedrun: Official failed ({e}), fallback also yielded 0.")
                self.last_mode = "fallback"
                self.index_used = self.fallback_url
                self.active_batches = self._rank_active_cohorts(companies)
                return companies

    def _rank_active_cohorts(self, companies) -> list[str]:
        """Newest Speedrun cohorts first.

        Cohort labels are strictly sequential (SR001..SR007), so unlike YC batches
        the label itself is a reliable ordering key.
        """
        cohorts = {c.batch for c in companies if c.batch}
        ranked = sorted(cohorts, reverse=True)
        return ranked[: self.active_batch_count]

    @classmethod
    def parse_primary_api(cls, data: dict, base_url: str = "https://speedrun.a16z.com/companies") -> list[Company]:
        companies = {}
        for c in data.get("results", []):
            slug = c.get("slug")
            if not slug:
                continue
            founders = c.get("founder_set", [])
            founder_names = [f"{f.get('first_name', '')} {f.get('last_name', '')}".strip() for f in founders]
            desc = c.get("description", "") or c.get("preamble", "")
            if founder_names:
                desc = f"Founders: {', '.join(founder_names)}. {desc}"

            companies[slug] = Company(
                name=c.get("name", slug)[:100],
                source=cls.name,
                external_id=slug,
                url=f"{base_url.rstrip('/')}/{slug}",
                batch=c.get("cohort"),
                description=desc,
                domain=c.get("website_url", ""),
                raw=c,
            )
        return list(companies.values())

    @classmethod
    def parse_fallback(
        cls,
        text: str,
        base_url: str = "https://speedrun-talent-network.com/collections/speedrun-companies",
    ) -> list[Company]:
        soup = BeautifulSoup(text, "html.parser")
        companies: dict[str, Company] = {}

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if any(part in href for part in ["/collections/", "/jobs", "/sign-in", "/apply"]):
                continue

            context = _node_context(anchor)
            cohort = re.search(r"\b(?:a16z\s*)?sr\s*0*(\d{1,3})\b", context, re.IGNORECASE)
            if not cohort:
                continue

            name = " ".join(anchor.stripped_strings).strip()
            if not name:
                continue

            batch = f"SR{int(cohort.group(1)):03d}"
            external_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            external_id = f"{external_id}:{batch.lower()}"
            companies[external_id] = Company(
                name=name[:100],
                source=cls.name,
                external_id=external_id,
                url=urljoin(base_url, href),
                batch=batch,
                raw={"context": context[:1500], "directory": "talent-network-fallback"},
            )

        return list(companies.values())
