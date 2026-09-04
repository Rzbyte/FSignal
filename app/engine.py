"""State transition engine for official and social discoveries.

The engine persists state before notifying Slack. This makes discovery durable,
keeps alerts idempotent, and allows transient Slack failures to retry safely.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import settings
from .intelligence import assess_signal
from .matcher import company_key, match_official, resolve_official

#: After this many attempts an alert is retired rather than retried forever.
MAX_ALERT_ATTEMPTS = 5

#: Failures that belong to one specific message. Everything else is assumed to be
#: global (auth, network, rate limit) and pauses the flush instead, because
#: burning through the queue against a dead token would lose every alert in it.
PER_ALERT_FAILURES = (
    "invalid_blocks",
    "msg_too_long",
    "invalid_arguments",
    "message_not_found",
    "thread_not_found",
    "unknown outbox alert kind",
    "missing outbox target",
)


def _is_per_alert_failure(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in PER_ALERT_FAILURES)


def _require(row, description: str):
    """The row an outbox entry points at, or a failure that only kills that entry.

    A dangling reference used to surface as an ``AttributeError`` from inside the
    notifier, which is not in ``PER_ALERT_FAILURES`` -- so one unresolvable row
    retried five times and held every alert behind it in the queue.
    """
    if row is None:
        raise RuntimeError(f"missing outbox target: {description}")
    return row


class RadarEngine:
    def __init__(self, db, notifier):
        self.db = db
        self.notifier = notifier
        # Serialise concurrent flush calls from multiple source tasks.
        self._flush_lock = asyncio.Lock()

    async def ingest_official(self, companies, alert_new: bool = False) -> int:
        """Persist an official snapshot and enqueue only incremental additions.

        The whole snapshot is written in one transaction: a full YC crawl carries
        thousands of rows, and a connection per row makes the baseline scan
        unusably slow.
        """
        upserts = self.db.upsert_companies(companies)
        additions = [
            (company, official_id)
            for company, (official_id, is_new) in zip(companies, upserts)
            if is_new
        ]
        if not additions:
            return 0

        self.db.record_timeline_many(
            [
                {
                    "event_key": f"official:{company.source}:{company.external_id}:detected",
                    "event_type": "official_detected",
                    "official_company_id": official_id,
                    "source": company.source,
                    "metadata": {
                        "name": company.name,
                        "batch": company.batch,
                        "url": company.url,
                    },
                }
                for company, official_id in additions
            ]
        )

        if alert_new:
            # Read once. Re-reading the ghost list per company would be quadratic
            # on any crawl that adds more than a handful of listings.
            ghosts = self.db.list_ghosts(500)
            for company, official_id in additions:
                official = self.db.get_official(official_id)
                matching_ghost_exists = any(
                    match_official(
                        ghost.get("company_name"), ghost.get("company_domain"), [official]
                    )
                    for ghost in ghosts
                )
                # A matching ghost gets the stronger confirmation alert during
                # reconciliation, avoiding a duplicate "new official" notification.
                if not matching_ghost_exists:
                    self.db.enqueue_alert(
                        f"official:{company.source}:{company.external_id}",
                        "official",
                        official_company_id=official_id,
                    )
        return len(additions)

    async def ingest_social(self, signals) -> int:
        """Adjudicate social discoveries and persist the ones worth keeping.

        Every candidate produces a ledger row whether or not it becomes an alert.
        A quiet day should be visibly a quiet day -- "we evaluated 139 candidates
        and alerted on one" -- rather than indistinguishable from a broken bot.
        """
        official_companies = self.db.list_official()
        ledger: list[dict] = []
        created = 0

        # An EARLY verdict is only as good as the snapshot it was checked against.
        # If that snapshot is stale, the honest answer is "possible", not "early".
        fresh_sources = self._fresh_snapshot_sources()
        # Companies announced earlier in *this* batch, so three simultaneous posts
        # about one company still produce one alert plus two corroborations.
        announced: set[str] = set()

        for signal in signals:
            extraction = getattr(signal, "extraction", None)

            # No defensible company identity: never alerted, always recorded.
            if extraction is None or not extraction.is_usable:
                ledger.append(
                    {
                        "source": signal.source,
                        "external_id": signal.external_id,
                        "url": signal.url,
                        "company_name": signal.company_name,
                        "batch": signal.batch,
                        "program": signal.program,
                        "verdict": "suppressed",
                        "reason": getattr(extraction, "reason", "no_extraction"),
                        "confidence": 0,
                    }
                )
                continue

            check = resolve_official(
                signal.company_name,
                signal.company_domain,
                official_companies,
                signal.batch,
            )
            match = check.match
            signal.official_check = check.as_dict()
            signal.official_check["checked_at"] = datetime.now(timezone.utc).isoformat()
            snapshot_source = (
                "speedrun" if signal.program == "speedrun" else "yc_directory"
            )
            snapshot = self.db.get_snapshot(snapshot_source) or {}
            signal.official_check["snapshot_source"] = snapshot_source
            signal.official_check["snapshot_size"] = snapshot.get("size")
            signal.official_check["snapshot_taken_at"] = snapshot.get("taken_at")
            assess_signal(signal, match)

            # The task values pre-directory founder announcements. A post that
            # already matches an official company is kept for audit and dedup but
            # is not represented as early. A weak-but-plausible one becomes
            # "possible": persisted and digestible, never a top-level alert.
            if match:
                initial_status, verdict, reason = (
                    "already_official",
                    "already_official",
                    "already_official",
                )
            elif snapshot_source not in fresh_sources:
                initial_status, verdict, reason = (
                    "possible",
                    "possible",
                    "stale_official_snapshot",
                )
            elif signal.confidence < settings.min_signal_confidence:
                initial_status, verdict, reason = (
                    "possible",
                    "possible",
                    "below_confidence_threshold",
                )
            else:
                initial_status, verdict, reason = "ghost", "alerted", None

            entry = {
                "source": signal.source,
                "external_id": signal.external_id,
                "url": signal.url,
                "company_name": signal.company_name,
                "batch": signal.batch,
                "program": signal.program,
                "verdict": verdict,
                "reason": reason,
                "confidence": signal.confidence,
            }
            ledger.append(entry)

            signal.company_key = company_key(
                signal.company_name, signal.company_domain, signal.program, signal.batch
            )
            signal_id, is_new = self.db.insert_signal(
                signal, initial_status, match["id"] if match else None
            )
            # A ledger row saying "alerted" with no way to reach the signal it
            # alerted on is half an audit trail. Written after the insert, which
            # is where the id comes from.
            entry["signal_id"] = signal_id
            if not is_new:
                continue

            created += 1
            self.db.record_timeline(
                f"signal:{signal.source}:{signal.external_id}:detected",
                "social_detected",
                signal_id=signal_id,
                official_company_id=match["id"] if match else None,
                source=signal.source,
                event_at=signal.detected_at.isoformat(),
                metadata={
                    "company": signal.company_name or signal.company_domain,
                    "confidence": signal.confidence,
                    "confidence_label": signal.confidence_label,
                    "gtm_score": signal.gtm_score,
                    "gtm_priority": signal.gtm_priority,
                },
            )

            if initial_status == "ghost":
                self.db.record_timeline(
                    f"signal:{signal.source}:{signal.external_id}:ghost",
                    "ghost_classified",
                    signal_id=signal_id,
                    source=signal.source,
                    event_at=signal.detected_at.isoformat(),
                    metadata={"reason": "No official directory match at detection time"},
                )
                # One alert per company, not per post. A second independent
                # source for a company already announced becomes corroboration in
                # the same Slack thread -- useful evidence, not a second alert.
                already = (
                    signal.company_key in announced
                    or self.db.company_alert(signal.company_key, "ghost") is not None
                )
                announced.add(signal.company_key)
                self.db.enqueue_alert(
                    (
                        f"corroboration:{signal.company_key}:{signal.source}:{signal.external_id}"
                        if already
                        else f"ghost:{signal.company_key}"
                    ),
                    "corroboration" if already else "ghost",
                    signal_id=signal_id,
                )
            elif match:
                self.db.record_timeline(
                    f"signal:{signal.source}:{signal.external_id}:already-official",
                    "already_official",
                    signal_id=signal_id,
                    official_company_id=match["id"],
                    source=signal.source,
                    event_at=signal.detected_at.isoformat(),
                )

        self.db.record_candidates(ledger)
        return created

    def _fresh_snapshot_sources(self) -> set[str]:
        """Official sources whose snapshot is recent enough to adjudicate against."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=settings.snapshot_max_age_minutes
        )
        fresh = set()
        for snapshot in self.db.snapshots():
            try:
                taken_at = datetime.fromisoformat(snapshot["taken_at"])
            except (TypeError, ValueError):
                continue
            if taken_at >= cutoff:
                fresh.add(snapshot["source"])
        return fresh

    async def reconcile_ghosts(self) -> int:
        """Promote old Ghost signals when an official company appears later."""
        official_companies = self.db.list_official()
        confirmed_count = 0

        for ghost in self.db.list_ghosts(500):
            match = match_official(
                ghost.get("company_name"),
                ghost.get("company_domain"),
                official_companies,
                ghost.get("batch"),
            )
            if not match:
                continue

            key = ghost.get("company_key") or company_key(
                ghost.get("company_name"),
                ghost.get("company_domain"),
                ghost.get("program") or "yc",
                ghost.get("batch"),
            )
            confirmed_at = self.db.confirm_signal(ghost["id"], match["id"])
            self.db.record_timeline(
                f"signal:{ghost['source']}:{ghost['external_id']}:confirmed",
                "official_confirmed",
                signal_id=ghost["id"],
                official_company_id=match["id"],
                source=match.get("source"),
                event_at=confirmed_at,
                metadata={"official_name": match.get("name"), "official_url": match.get("url")},
            )
            # Exactly one confirmation per company, however many posts
            # corroborated it.
            self.db.enqueue_alert(
                f"confirmed:{key}",
                "confirmed",
                signal_id=ghost["id"],
                official_company_id=match["id"],
            )
            confirmed_count += 1

        return confirmed_count

    async def flush_alerts(self, limit: int = 100) -> dict:
        """Deliver pending alerts and leave failures queued for a later retry.

        Concurrent callers (from independent source tasks) are serialised by a
        lock so the same alert is never delivered twice.  The second caller
        returns immediately if a flush is already in progress.
        """
        if self._flush_lock.locked():
            stats = self.db.outbox_stats()
            return {
                "sent": 0,
                "failed": 0,
                "dead": 0,
                "pending": stats["pending"],
                "dead_total": stats["dead"],
                "error": None,
            }
        async with self._flush_lock:
            sent = 0
            failed = 0
            dead = 0
            last_error = None

            for alert in self.db.pending_alerts(limit):
                try:
                    signal = None
                    if alert["kind"] == "ghost":
                        signal = _require(
                            self.db.get_signal(alert["signal_id"]),
                            f"signal {alert['signal_id']}",
                        )
                        response = await self.notifier.send_ghost(signal)
                        self.db.mark_alerted(alert["signal_id"], "ghost")
                        # Remember the Slack message so later corroboration for
                        # the same company can reply in its thread.
                        self.db.record_company_alert(
                            signal.get("company_key"),
                            "ghost",
                            signal["id"],
                            (response or {}).get("ts"),
                        )
                    elif alert["kind"] == "corroboration":
                        signal = _require(
                            self.db.get_signal(alert["signal_id"]),
                            f"signal {alert['signal_id']}",
                        )
                        parent = self.db.company_alert(signal.get("company_key"), "ghost")
                        await self.notifier.send_corroboration(
                            signal, (parent or {}).get("slack_ts")
                        )
                        self.db.mark_alerted(alert["signal_id"], "ghost")
                    elif alert["kind"] == "confirmed":
                        signal = _require(
                            self.db.get_signal(alert["signal_id"]),
                            f"signal {alert['signal_id']}",
                        )
                        company = _require(
                            self.db.get_official(alert["official_company_id"]),
                            f"official company {alert['official_company_id']}",
                        )
                        await self.notifier.send_confirmed(signal, company)
                        self.db.mark_alerted(alert["signal_id"], "confirmed")
                        self.db.record_company_alert(
                            signal.get("company_key"), "confirmed", signal["id"], None
                        )
                    elif alert["kind"] == "official":
                        company = _require(
                            self.db.get_official(alert["official_company_id"]),
                            f"official company {alert['official_company_id']}",
                        )
                        await self.notifier.send_official(company)
                    else:
                        raise RuntimeError(f"Unknown outbox alert kind: {alert['kind']}")

                    self.db.mark_alert_sent(alert["id"])
                    # Every delivered alert lands on the timeline, including a
                    # NEW OFFICIAL one -- which has no signal behind it, and used
                    # to leave no trace of having been sent at all.
                    self.db.record_timeline(
                        f"alert:{alert['dedupe_key']}:sent",
                        "slack_alert_sent",
                        signal_id=signal["id"] if signal else None,
                        official_company_id=alert.get("official_company_id"),
                        source="slack",
                        metadata={"kind": alert["kind"]},
                    )
                    sent += 1
                except Exception as exc:
                    last_error = str(exc)[:1000]
                    self.db.mark_alert_error(alert["id"], last_error)
                    failed += 1

                    attempts = (alert.get("attempts") or 0) + 1
                    if attempts >= MAX_ALERT_ATTEMPTS or _is_per_alert_failure(exc):
                        # One undeliverable message must never mute every alert
                        # behind it in the queue.
                        self.db.mark_alert_dead(alert["id"], last_error)
                        dead += 1
                        continue

                    # Otherwise assume the failure is global (token, network,
                    # rate limit) and retry the ordered queue on the next flush.
                    break

            stats = self.db.outbox_stats()
            return {
                "sent": sent,
                "failed": failed,
                "dead": dead,
                "pending": stats["pending"],
                "dead_total": stats["dead"],
                "error": last_error,
            }
