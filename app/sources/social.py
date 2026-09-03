"""Social discovery adapters for X and public LinkedIn signals."""

from __future__ import annotations

import hashlib
import re

import httpx

from ..config import settings
from ..extract import enrich_signal
from ..models import SocialSignal
from ..targeting import (
    SocialTargets,
    linkedin_queries,
    x_indexed_queries,
    x_queries,
)


# Sources deliberately do no filtering. Every candidate they see is handed to the
# engine, which adjudicates it against the official snapshot and records the
# verdict in the suppression ledger. Filtering here would throw away exactly the
# evidence that makes the precision claim auditable.


#: A public X post URL, and nothing else. The indexed provider also returns
#: profile pages, ``/with_replies``, ``/reposts`` and language variants, none of
#: which is an announcement. The trailing part is deliberately unanchored so
#: ``.../status/123/photo/1`` still yields the post it belongs to.
_X_STATUS_URL = re.compile(
    r"^https?://(?:mobile\.|www\.)?x\.com/([A-Za-z0-9_]{1,15})/status/(\d+)",
    re.IGNORECASE,
)

#: ``Tsenta (YC S26) on X`` / ``Alex Danilowicz on X: "after we got into yc...``
#:
#: The connector is matched as *any* short word rather than the literal "on":
#: the provider serves whatever locale it ranked the result in, and a live run
#: returned ``Adalat AI (YC F26) على X: "..."``. Titles that begin with the post
#: text carry no author at all, so a leading quote disqualifies the match rather
#: than yielding a fragment of somebody's announcement as their name.
_X_TITLE_AUTHOR = re.compile(
    r'^(?!["“])(.{1,80}?)\s+\w{1,8}\s+X\s*(?::|$)', re.IGNORECASE
)

#: ``x.com/i/status/123`` is a real post URL, but ``i`` is a routing segment
#: rather than anybody's handle.
_X_RESERVED_HANDLES = {"i", "home", "search", "explore", "notifications", "messages"}


def _fallback_worthy(reason: str) -> bool:
    """True when the native X path failed for a reason the fallback can survive.

    A depleted account and an absent token are both "this deployment cannot reach
    the paid API" -- the hunt is still possible. ``waiting:`` and genuine HTTP
    faults are not: retrying them through a second provider would hide a real
    problem behind a second-best answer.
    """
    return reason.startswith(("billing_blocked:", "not_configured:"))


class XSource:
    """Discover founder launch announcements on X.

    Queries are generated from whichever batches the official directories report
    as currently filling. A batch that is already fully published cannot produce
    an early signal, so hunting one is worse than not hunting at all.

    Two paths, in order of fidelity. X's own recent search carries the metadata
    this product is best with -- author bio, profile URL, exact post time -- but
    it is a paid product, and an account without a plan gets ``402``. Rather than
    let the source go dark on a billing state, the same vocabulary is then run
    against *publicly indexed* X URLs. ``last_mode`` records which path answered,
    and every layer above -- health, ledger, Slack badge -- reports it, so an
    indexed result is never presented as a native one.
    """

    name = "x"
    endpoint = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, targets: SocialTargets | None = None, db=None):
        # The scanner refreshes both of these before each run.
        self._targets = targets or SocialTargets()
        self.db = db
        self.last_mode: str | None = None
        self.fallback = XIndexedSource(self._targets)

    @property
    def targets(self) -> SocialTargets:
        return self._targets

    @targets.setter
    def targets(self, value: SocialTargets) -> None:
        # The scanner assigns this directly each run; the fallback hunts the same
        # batches or it is not the same source.
        self._targets = value
        self.fallback.targets = value

    def build_queries(self) -> list[str]:
        return x_queries(self.targets)

    def _watermark_key(self, query: str) -> str:
        return f"x:since_id:{hashlib.sha256(query.encode()).hexdigest()[:16]}"

    async def collect(self) -> list[SocialSignal]:
        try:
            signals = await self.collect_native()
        except RuntimeError as exc:
            if not _fallback_worthy(str(exc)) or not settings.serper_api_key:
                raise
            self.last_mode = "indexed_fallback"
            return await self.fallback.collect()
        self.last_mode = "native"
        return signals

    async def collect_native(self) -> list[SocialSignal]:
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
                        collection_mode="native",
                        raw={"tweet": tweet, "user": user},
                    )
                    enrich_signal(signal)
                    signals.append(signal)

        return list({signal.external_id: signal for signal in signals}.values())


class XIndexedSource:
    """The X hunt, run against publicly indexed post URLs.

    Used only when :class:`XSource` cannot reach X's own recent search. It reads
    the same public posts a logged-out visitor sees, through the search provider
    already configured for LinkedIn, so no new credential is introduced.

    What it gives up versus the native API is real: no author bio, no profile
    URL, no exact post timestamp, and the provider's own ranking rather than a
    complete recent window. What it keeps is the part the product is built on --
    the post URL, the announcement text, and the founder's handle.
    """

    name = "x"
    endpoint = "https://google.serper.dev/search"

    def __init__(self, targets: SocialTargets | None = None):
        self.targets = targets or SocialTargets()

    def build_queries(self) -> list[str]:
        return x_indexed_queries(self.targets)

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
                        "tbs": f"qdr:{settings.x_indexed_lookback}",
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"serper: API Error {response.status_code}: {response.text[:200]}"
                    )
                response.raise_for_status()
                signals.extend(self.parse_response(response.json()))

        return list({signal.external_id: signal for signal in signals}.values())

    @classmethod
    def parse_response(cls, payload: dict) -> list[SocialSignal]:
        signals: list[SocialSignal] = []
        for item in payload.get("organic", []):
            match = _X_STATUS_URL.match(item.get("link", "") or "")
            if not match:
                continue
            handle, tweet_id = match.group(1), match.group(2)

            title = item.get("title", "") or ""
            snippet = item.get("snippet", "") or ""
            author = _X_TITLE_AUTHOR.match(title)
            author_name = author.group(1).strip() if author else None

            # A result title is the account name followed by the opening of the
            # post the snippet already carries in full. Concatenating both put
            # the announcement in the Slack excerpt twice. The account name is
            # the part the title genuinely adds -- it is often where the batch
            # is stated ("Adalat AI (YC F26)") -- so that is what is kept.
            text = " ".join(
                part for part in [snippet or title, author_name] if part
            ).strip()

            signal = SocialSignal(
                source=cls.name,
                # The post id, not a hash of the URL. The native collector keys on
                # the same id, so a post seen both ways collapses to one row under
                # UNIQUE(source, external_id) instead of alerting twice.
                external_id=tweet_id,
                url=f"https://x.com/{handle}/status/{tweet_id}",
                text=text,
                author_name=author_name,
                author_handle=(
                    None if handle.lower() in _X_RESERVED_HANDLES else handle
                ),
                collection_mode="indexed_fallback",
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
