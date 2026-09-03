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
    """Discover founder launch announcements through X via Serper Google Search.

    Bypasses the X API billing limits by searching indexed public posts.
    """

    name = "x"
    endpoint = "https://google.serper.dev/search"

    def __init__(self, targets: SocialTargets | None = None, db=None):
        self.targets = targets or SocialTargets()
        self.db = db

    def build_queries(self) -> list[str]:
        return x_queries(self.targets)

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
            if "x.com/" not in link and "twitter.com/" not in link:
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
