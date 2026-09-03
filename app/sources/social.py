"""Social discovery adapters for X and public LinkedIn signals."""

from __future__ import annotations

import hashlib

import httpx

from ..config import settings
from ..extract import enrich_signal
from ..models import SocialSignal
from ..targeting import SocialTargets, linkedin_queries, x_queries


# Sources deliberately do no filtering. Every candidate they see is handed to the
# engine, which adjudicates it against the official snapshot and records the
# verdict in the suppression ledger. Filtering here would throw away exactly the
# evidence that makes the precision claim auditable.


class XSource:
    """Discover founder launch announcements through X recent search.

    Queries are generated from whichever batches the official directories report
    as currently filling. A batch that is already fully published cannot produce
    an early signal, so hunting one is worse than not hunting at all.
    """

    name = "x"
    endpoint = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, targets: SocialTargets | None = None, db=None):
        # The scanner refreshes both of these before each run.
        self.targets = targets or SocialTargets()
        self.db = db

    def build_queries(self) -> list[str]:
        return x_queries(self.targets)

    def _watermark_key(self, query: str) -> str:
        return f"x:since_id:{hashlib.sha256(query.encode()).hexdigest()[:16]}"

    async def collect(self) -> list[SocialSignal]:
        if not settings.x_bearer_token:
            raise RuntimeError("not_configured: X_BEARER_TOKEN is missing")
        if self.targets.is_empty:
            raise RuntimeError(
                "waiting: no active accelerator batch known yet - the official "
                "directory scan has not completed"
            )

        headers = {
            "Authorization": f"Bearer {settings.x_bearer_token}",
            "User-Agent": settings.user_agent,
        }
        signals: list[SocialSignal] = []

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds, headers=headers
        ) as client:
            for query in self.build_queries():
                params = {
                    "query": query,
                    "max_results": max(10, min(settings.x_max_results, 100)),
                    "tweet.fields": "created_at,entities,author_id",
                    "expansions": "author_id",
                    "user.fields": "name,username,description,url",
                }
                # Only ask for posts newer than the last one processed. This bounds
                # cost on a metered API; the UNIQUE(source, external_id) constraint
                # remains the correctness guarantee for dedup.
                since_id = self.db.get_watermark(self._watermark_key(query)) if self.db else None
                if since_id:
                    params["since_id"] = since_id

                response = await client.get(self.endpoint, params=params)
                if response.status_code == 402:
                    # Vendor billing, not an implementation fault. Reported
                    # distinctly so /health states the real reason.
                    raise RuntimeError(
                        f"billing_blocked: X API credits are depleted ({response.text[:120]})"
                    )
                response.raise_for_status()
                payload = response.json()

                if self.db:
                    ids = [tweet["id"] for tweet in payload.get("data", []) if tweet.get("id")]
                    if ids:
                        self.db.set_watermark(
                            self._watermark_key(query), max(ids, key=int)
                        )
                users = {
                    user["id"]: user
                    for user in payload.get("includes", {}).get("users", [])
                }

                for tweet in payload.get("data", []):
                    user = users.get(tweet.get("author_id"), {})
                    username = user.get("username")
                    source_url = (
                        f"https://x.com/{username}/status/{tweet['id']}"
                        if username
                        else f"https://x.com/i/status/{tweet['id']}"
                    )

                    expanded_urls = [
                        item.get("expanded_url", "")
                        for item in tweet.get("entities", {}).get("urls", [])
                        if item.get("expanded_url")
                    ]
                    combined_text = " ".join(
                        part
                        for part in [
                            tweet.get("text", ""),
                            user.get("description", ""),
                            user.get("url", ""),
                            *expanded_urls,
                        ]
                        if part
                    )

                    signal = SocialSignal(
                        source=self.name,
                        external_id=tweet["id"],
                        url=source_url,
                        text=combined_text,
                        author_name=user.get("name"),
                        author_handle=username,
                        raw={"tweet": tweet, "user": user},
                    )
                    enrich_signal(signal)
                    signals.append(signal)

        return list({signal.external_id: signal for signal in signals}.values())


class LinkedInSource:
    """Discover public LinkedIn launch signals through an indexed-search provider.

    LinkedIn does not expose unrestricted public-post search through its standard
    developer APIs. This adapter searches public indexed LinkedIn URLs instead of
    scraping authenticated LinkedIn pages. The provider is deliberately isolated
    so it can be replaced if the client later receives approved LinkedIn access.
    """

    name = "linkedin"
    endpoint = "https://google.serper.dev/search"

    def __init__(self, targets: SocialTargets | None = None):
        # The scanner refreshes this from the official snapshot before each run.
        self.targets = targets or SocialTargets()

    def build_queries(self) -> list[str]:
        return linkedin_queries(self.targets)

    async def collect(self) -> list[SocialSignal]:
        if not settings.serper_api_key:
            raise RuntimeError("not_configured: SERPER_API_KEY is missing")
        if self.targets.is_empty:
            raise RuntimeError(
                "waiting: no active accelerator batch known yet - the official "
                "directory scan has not completed"
            )

        headers = {
            "X-API-KEY": settings.serper_api_key,
            "Content-Type": "application/json",
        }
        signals: list[SocialSignal] = []

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds, headers=headers
        ) as client:
            for query in self.build_queries():
                response = await client.post(
                    self.endpoint,
                    json={
                        "q": query,
                        "num": 10,
                        "tbs": f"qdr:{settings.linkedin_lookback}",
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"serper: API Error {response.status_code}: {response.text[:200]}")
                response.raise_for_status()
                signals.extend(self.parse_response(response.json()))

        return list({signal.external_id: signal for signal in signals}.values())

    @classmethod
    def parse_response(cls, payload: dict) -> list[SocialSignal]:
        signals: list[SocialSignal] = []
        for item in payload.get("organic", []):
            link = item.get("link", "")
            if "linkedin.com/" not in link:
                continue

            text = " ".join(
                part for part in [item.get("title", ""), item.get("snippet", "")] if part
            ).strip()
            signal = SocialSignal(
                source=cls.name,
                external_id=hashlib.sha256(link.encode()).hexdigest()[:24],
                url=link,
                text=text,
                raw=item,
            )
            enrich_signal(signal)
            signals.append(signal)
        return signals
