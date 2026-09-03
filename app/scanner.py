"""Orchestrates all source collectors and the detection engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .engine import RadarEngine
from .slack import SlackNotifier
from .targeting import SocialTargets
from .sources.official import SpeedrunSource, YCDirectorySource
from .sources.social import LinkedInSource, XSource


class Scanner:
    def __init__(self, db):
        self.db = db
        self.engine = RadarEngine(db, SlackNotifier())

        # This registry is the extension point for future social platforms.
        self.official_sources = [YCDirectorySource(), SpeedrunSource()]
        self.social_sources = [XSource(db=db), LinkedInSource()]
        self.lock = asyncio.Lock()

    async def scan_all(self) -> dict:
        """Run every source once, without allowing overlapping scans."""
        if self.lock.locked():
            return {"status": "already_running"}

        async with self.lock:
            result = {"official": {}, "social": {}, "reconciled": 0, "notifications": {}}

            # Official directories are scanned first so social posts can be
            # classified against the freshest source-of-truth snapshot.
            for source in self.official_sources:
                result["official"][source.name] = await self._run_source(
                    source, official=True
                )

            result["reconciled"] = await self.engine.reconcile_ghosts()

            for source in self.social_sources:
                result["social"][source.name] = await self._run_source(
                    source, official=False
                )

            # Persistent outbox delivery is intentionally separate from source
            # ingestion so a transient Slack failure never loses a discovery.
            result["notifications"] = await self.engine.flush_alerts()
            return result

    async def run_named_source(self, name: str) -> dict:
        """Run a single source by name and flush pending Slack alerts.

        Called by the per-source scheduler on each source's own interval.
        Raises ValueError for unknown source names (callers should guard).
        """
        for source in self.official_sources:
            if source.name == name:
                result = await self._run_source(source, official=True)
                await self.engine.flush_alerts()
                return result
        for source in self.social_sources:
            if source.name == name:
                result = await self._run_source(source, official=False)
                await self.engine.flush_alerts()
                return result
        raise ValueError(f"Unknown source: {name!r}")

    async def run_reconciliation(self) -> dict:
        """Reconcile ghost signals against official directories and flush alerts.

        Called by the per-source scheduler on the ghost-recheck interval.
        """
        reconciled = await self.engine.reconcile_ghosts()
        flush = await self.engine.flush_alerts()
        return {"reconciled": reconciled, "notifications": flush}

    async def _run_source(self, source, official: bool) -> dict:

        started_at = datetime.now(timezone.utc)
        status = "ok"
        error = None
        count = 0

        try:
            if not official:
                # Social sources hunt whatever the official directories say is
                # currently filling, so the targets are resolved per run rather
                # than baked into the adapter.
                source.targets = SocialTargets.from_db(self.db)
            items = await source.collect()
            if official:
                # The first snapshot establishes a baseline. We only alert on
                # official companies discovered after that initial snapshot.
                baseline = not self.db.has_official_source(source.name)
                count = await self.engine.ingest_official(
                    items, alert_new=not baseline
                )
                # Every EARLY verdict cites the corpus it was checked against, so
                # record how large that corpus is and when it was taken.
                self.db.record_snapshot(
                    source.name,
                    self.db.count_official(source.name),
                    mode=getattr(source, "last_mode", None),
                    index_used=getattr(source, "index_used", None),
                    active_batches=getattr(source, "active_batches", None),
                )
            else:
                count = await self.engine.ingest_social(items)
        except RuntimeError as exc:
            error = str(exc)[:1000]
            if error.startswith("not_configured:"):
                status = "not_configured"
            elif error.startswith("billing_blocked:"):
                # The adapter is correct; the vendor account is out of credit.
                status = "billing_blocked"
            elif error.startswith("waiting:"):
                # A legitimate "nothing to do yet" state, not a fault.
                status = "waiting"
            else:
                status = "error"
        except Exception as exc:  # Source failures must not stop other sources.
            error = str(exc)[:1000]
            status = "error"

        finished_at = datetime.now(timezone.utc)
        self.db.record_source_run(
            source.name,
            status,
            count,
            started_at.isoformat(),
            finished_at.isoformat(),
            error,
        )
        return {"status": status, "items": count, "error": error}
