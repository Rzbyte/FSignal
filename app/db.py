"""SQLite persistence for monitoring state, intelligence, alerts, and Pond runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def normalize_name(value: str | None) -> str:
    """Normalize a company name for conservative exact-ish matching."""
    return "".join(ch for ch in (value or "").lower().strip() if ch.isalnum())


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS official_companies(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT,
    batch TEXT,
    domain TEXT,
    url TEXT,
    description TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS social_signals(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    text TEXT,
    author_name TEXT,
    author_handle TEXT,
    company_name TEXT,
    normalized_company_name TEXT,
    company_domain TEXT,
    company_key TEXT,
    batch TEXT,
    program TEXT,
    confidence INTEGER,
    confidence_label TEXT,
    evidence_json TEXT,
    gtm_score INTEGER,
    gtm_priority TEXT,
    gtm_reasons_json TEXT,
    status TEXT NOT NULL,
    official_company_id INTEGER,
    detected_at TEXT NOT NULL,
    confirmed_at TEXT,
    official_check_json TEXT,
    alerted_ghost_at TEXT,
    alerted_confirmed_at TEXT,
    raw_json TEXT,
    -- Which collection path produced this signal ("native", "indexed_fallback"),
    -- for a source that has more than one. NULL otherwise.
    collection_mode TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS watermarks(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_alerts(
    company_key TEXT NOT NULL,
    stage TEXT NOT NULL,
    signal_id INTEGER,
    slack_ts TEXT,
    alerted_at TEXT NOT NULL,
    PRIMARY KEY(company_key, stage)
);

CREATE TABLE IF NOT EXISTS candidate_ledger(
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    company_name TEXT,
    batch TEXT,
    program TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    confidence INTEGER,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY(source, external_id)
);

CREATE TABLE IF NOT EXISTS official_snapshots(
    source TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mode TEXT,
    index_used TEXT,
    active_batches TEXT,
    taken_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_runs(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    -- Which path answered, when a source has more than one. NULL for sources
    -- that only ever have the one.
    mode TEXT
);

CREATE TABLE IF NOT EXISTS pond_runs(
    run_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_outbox(
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    signal_id INTEGER,
    official_company_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    dead_at TEXT
);

CREATE TABLE IF NOT EXISTS timeline_events(
    id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    signal_id INTEGER,
    official_company_id INTEGER,
    source TEXT,
    event_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_social_status_detected
ON social_signals(status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_signal
ON timeline_events(signal_id, event_at ASC);
CREATE INDEX IF NOT EXISTS idx_source_runs_source
ON source_runs(source, id DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_evaluated
ON candidate_ledger(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_company_key
ON social_signals(company_key);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            # Safe additive migrations for users upgrading an earlier V1 DB.
            self._add_missing_columns(
                connection,
                "social_signals",
                {
                    "confidence_label": "TEXT",
                    "evidence_json": "TEXT",
                    "gtm_score": "INTEGER",
                    "gtm_priority": "TEXT",
                    "gtm_reasons_json": "TEXT",
                    "official_check_json": "TEXT",
                    "company_key": "TEXT",
                },
            )
            self._add_missing_columns(
                connection, "official_snapshots", {"active_batches": "TEXT"}
            )
            self._add_missing_columns(connection, "alert_outbox", {"dead_at": "TEXT"})
            self._add_missing_columns(connection, "source_runs", {"mode": "TEXT"})
            self._add_missing_columns(
                connection, "social_signals", {"collection_mode": "TEXT"}
            )

    @staticmethod
    def _add_missing_columns(connection, table: str, columns: dict[str, str]) -> None:
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column, sql_type in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def upsert_company(self, company):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            existed = connection.execute(
                "SELECT id FROM official_companies WHERE source=? AND external_id=?",
                (company.source, company.external_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO official_companies(
                    source, external_id, name, normalized_name, batch, domain,
                    url, description, first_seen_at, last_seen_at, raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,external_id) DO UPDATE SET
                    name=excluded.name,
                    normalized_name=excluded.normalized_name,
                    batch=excluded.batch,
                    domain=excluded.domain,
                    url=excluded.url,
                    description=excluded.description,
                    last_seen_at=excluded.last_seen_at,
                    raw_json=excluded.raw_json
                """,
                (
                    company.source,
                    company.external_id,
                    company.name,
                    normalize_name(company.name),
                    company.batch,
                    company.domain,
                    company.url,
                    company.description,
                    now,
                    now,
                    json.dumps(company.raw),
                ),
            )
            row = connection.execute(
                "SELECT id FROM official_companies WHERE source=? AND external_id=?",
                (company.source, company.external_id),
            ).fetchone()
            return row["id"], existed is None

    def upsert_companies(self, companies) -> list[tuple[int, bool]]:
        """Upsert a whole directory snapshot inside one transaction.

        `upsert_company` opens a connection per row, which is unusably slow for
        a full ~6k-company crawl. Returns (id, is_new) aligned with *companies*.
        """
        now = datetime.now(timezone.utc).isoformat()
        results: list[tuple[int, bool]] = []
        with self.connect() as connection:
            known = {
                (row["source"], row["external_id"]): row["id"]
                for row in connection.execute(
                    "SELECT id, source, external_id FROM official_companies"
                )
            }
            for company in companies:
                key = (company.source, company.external_id)
                connection.execute(
                    """
                    INSERT INTO official_companies(
                        source, external_id, name, normalized_name, batch, domain,
                        url, description, first_seen_at, last_seen_at, raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,external_id) DO UPDATE SET
                        name=excluded.name,
                        normalized_name=excluded.normalized_name,
                        batch=excluded.batch,
                        domain=excluded.domain,
                        url=excluded.url,
                        description=excluded.description,
                        last_seen_at=excluded.last_seen_at,
                        raw_json=excluded.raw_json
                    """,
                    (
                        company.source,
                        company.external_id,
                        company.name,
                        normalize_name(company.name),
                        company.batch,
                        company.domain,
                        company.url,
                        company.description,
                        now,
                        now,
                        json.dumps(company.raw),
                    ),
                )
                company_id = known.get(key)
                if company_id is None:
                    row = connection.execute(
                        "SELECT id FROM official_companies WHERE source=? AND external_id=?",
                        key,
                    ).fetchone()
                    results.append((row["id"], True))
                else:
                    results.append((company_id, False))
        return results

    def count_official(self, source: str | None = None) -> int:
        with self.connect() as connection:
            if source is None:
                return connection.execute(
                    "SELECT COUNT(*) FROM official_companies"
                ).fetchone()[0]
            return connection.execute(
                "SELECT COUNT(*) FROM official_companies WHERE source=?", (source,)
            ).fetchone()[0]

    def record_snapshot(
        self,
        source: str,
        size: int,
        mode: str | None = None,
        index_used: str | None = None,
        active_batches: list[str] | None = None,
        taken_at: str | None = None,
    ) -> None:
        """Record how large the official snapshot for *source* was, and when.

        Alerts cite this so an EARLY claim names the corpus it was checked
        against instead of asserting absence without evidence.
        """
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO official_snapshots"
                "(source,size,mode,index_used,active_batches,taken_at) VALUES(?,?,?,?,?,?)",
                (
                    source,
                    size,
                    mode,
                    index_used,
                    json.dumps(list(active_batches)) if active_batches else None,
                    taken_at or datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_snapshot(self, source: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM official_snapshots WHERE source=?", (source,)
            ).fetchone()
            return dict(row) if row else None

    def active_batches(self, source: str) -> list[str]:
        """Batches the last snapshot of *source* reported as currently filling.

        Social targeting reads this rather than a hardcoded batch string, so the
        bot retargets itself when a new batch opens.
        """
        snapshot = self.get_snapshot(source) or {}
        try:
            return list(json.loads(snapshot.get("active_batches") or "[]"))
        except (TypeError, json.JSONDecodeError):
            return []

    def snapshots(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM official_snapshots ORDER BY source"
                )
            ]

    def has_official_source(self, source: str) -> bool:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM official_companies WHERE source=?", (source,)
            ).fetchone()[0] > 0

    def get_official(self, company_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM official_companies WHERE id=?", (company_id,)
            ).fetchone()
            return dict(row) if row else None

    def insert_signal(self, signal, status: str, official_id: int | None = None):
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM social_signals WHERE source=? AND external_id=?",
                (signal.source, signal.external_id),
            ).fetchone()
            if existing:
                return existing["id"], False

            cursor = connection.execute(
                """
                INSERT INTO social_signals(
                    source, external_id, url, text, author_name, author_handle,
                    company_name, normalized_company_name, company_domain, company_key,
                    batch, program, confidence, confidence_label, evidence_json,
                    gtm_score, gtm_priority, gtm_reasons_json, status,
                    official_company_id, detected_at, official_check_json, raw_json,
                    collection_mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal.source,
                    signal.external_id,
                    signal.url,
                    signal.text,
                    signal.author_name,
                    signal.author_handle,
                    signal.company_name,
                    normalize_name(signal.company_name),
                    signal.company_domain,
                    getattr(signal, "company_key", None),
                    signal.batch,
                    signal.program,
                    signal.confidence,
                    getattr(signal, "confidence_label", "review"),
                    json.dumps(getattr(signal, "evidence", [])),
                    getattr(signal, "gtm_score", 0),
                    getattr(signal, "gtm_priority", "standard"),
                    json.dumps(getattr(signal, "gtm_reasons", [])),
                    status,
                    official_id,
                    signal.detected_at.isoformat(),
                    json.dumps(getattr(signal, "official_check", None) or {}),
                    json.dumps(signal.raw),
                    getattr(signal, "collection_mode", None),
                ),
            )
            return cursor.lastrowid, True

    def list_official(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM official_companies")]

    def list_ghosts(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM social_signals WHERE status='ghost' "
                    "ORDER BY gtm_score DESC, confidence DESC, detected_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def get_signal(self, signal_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM social_signals WHERE id=?", (signal_id,)
            ).fetchone()
            return dict(row) if row else None

    def mark_alerted(self, signal_id: int, kind: str) -> None:
        column = "alerted_ghost_at" if kind == "ghost" else "alerted_confirmed_at"
        with self.connect() as connection:
            connection.execute(
                f"UPDATE social_signals SET {column}=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), signal_id),
            )

    def confirm_signal(self, signal_id: int, official_id: int) -> str:
        confirmed_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "UPDATE social_signals SET status='confirmed', official_company_id=?, "
                "confirmed_at=? WHERE id=?",
                (official_id, confirmed_at, signal_id),
            )
        return confirmed_at

    def record_timeline(
        self,
        event_key: str,
        event_type: str,
        *,
        signal_id: int | None = None,
        official_company_id: int | None = None,
        source: str | None = None,
        event_at: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO timeline_events(
                    event_key,event_type,signal_id,official_company_id,source,event_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    event_key,
                    event_type,
                    signal_id,
                    official_company_id,
                    source,
                    event_at or datetime.now(timezone.utc).isoformat(),
                    json.dumps(metadata or {}),
                ),
            )

    def record_timeline_many(self, events: list[dict]) -> None:
        """Insert many timeline events in one transaction.

        A baseline directory crawl records thousands of events at once; one
        connection per event would make the first scan take minutes.
        """
        if not events:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                event["event_key"],
                event["event_type"],
                event.get("signal_id"),
                event.get("official_company_id"),
                event.get("source"),
                event.get("event_at") or now,
                json.dumps(event.get("metadata") or {}),
            )
            for event in events
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO timeline_events(
                    event_key,event_type,signal_id,official_company_id,source,event_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                rows,
            )

    def timeline(self, signal_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM timeline_events WHERE signal_id=? ORDER BY event_at ASC, id ASC",
                (signal_id,),
            )
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                except json.JSONDecodeError:
                    item["metadata"] = {}
                result.append(item)
            return result

    def record_source_run(
        self,
        source: str,
        status: str,
        count: int,
        started_at: str,
        finished_at: str,
        error: str | None = None,
        mode: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO source_runs(source,status,item_count,error,started_at,finished_at,mode) "
                "VALUES(?,?,?,?,?,?,?)",
                (source, status, count, error, started_at, finished_at, mode),
            )

    def source_status(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT r.*
                    FROM source_runs r
                    JOIN (
                        SELECT source, MAX(id) AS max_id
                        FROM source_runs
                        GROUP BY source
                    ) latest ON r.id = latest.max_id
                    ORDER BY r.source
                    """
                )
            ]

    def stats(self) -> dict:
        with self.connect() as connection:
            def count(sql: str) -> int:
                return connection.execute(sql).fetchone()[0]

            avg_lead = connection.execute(
                """
                SELECT AVG((julianday(confirmed_at)-julianday(detected_at))*24.0)
                FROM social_signals
                WHERE status='confirmed' AND confirmed_at IS NOT NULL
                """
            ).fetchone()[0]
            return {
                "ghosts": count("SELECT COUNT(*) FROM social_signals WHERE status='ghost'"),
                "high_priority_ghosts": count(
                    "SELECT COUNT(*) FROM social_signals WHERE status='ghost' AND gtm_priority='high'"
                ),
                "confirmed": count("SELECT COUNT(*) FROM social_signals WHERE status='confirmed'"),
                "signals": count("SELECT COUNT(*) FROM social_signals"),
                "official_companies": count("SELECT COUNT(*) FROM official_companies"),
                "average_early_lead_hours": round(avg_lead, 1) if avg_lead is not None else None,
            }

    def record_candidates(self, entries: list[dict]) -> None:
        """Record what every candidate was judged to be, and why.

        This is the suppression ledger. Without it, precision is a claim; with
        it, a reviewer can see the forty candidates that were rejected alongside
        the one that produced an alert, each with its reason code.
        """
        if not entries:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                entry["source"],
                entry["external_id"],
                entry.get("url"),
                entry.get("company_name"),
                entry.get("batch"),
                entry.get("program"),
                entry["verdict"],
                entry.get("reason"),
                entry.get("confidence"),
                entry.get("evaluated_at") or now,
            )
            for entry in entries
        ]
        with self.connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO candidate_ledger("
                "source,external_id,url,company_name,batch,program,verdict,reason,"
                "confidence,evaluated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

    def ledger_summary(self) -> dict:
        """Counts by verdict and by suppression reason, for /health and Slack."""
        with self.connect() as connection:
            verdicts = {
                row["verdict"]: row["n"]
                for row in connection.execute(
                    "SELECT verdict, COUNT(*) n FROM candidate_ledger GROUP BY verdict"
                )
            }
            reasons = {
                row["reason"]: row["n"]
                for row in connection.execute(
                    "SELECT reason, COUNT(*) n FROM candidate_ledger "
                    "WHERE reason IS NOT NULL GROUP BY reason ORDER BY n DESC"
                )
            }
            return {"verdicts": verdicts, "reasons": reasons,
                    "evaluated": sum(verdicts.values())}

    def recent_candidates(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM candidate_ledger ORDER BY evaluated_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def get_watermark(self, key: str) -> str | None:
        """Read a source cursor (e.g. the newest X post id already processed)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM watermarks WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_watermark(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO watermarks(key,value,updated_at) VALUES(?,?,?)",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    def company_alert(self, company_key: str, stage: str) -> dict | None:
        """Whether this company has already been announced at this stage."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM company_alerts WHERE company_key=? AND stage=?",
                (company_key, stage),
            ).fetchone()
            return dict(row) if row else None

    def record_company_alert(
        self, company_key: str, stage: str, signal_id: int | None, slack_ts: str | None
    ) -> None:
        """Remember the Slack message so corroboration can reply in its thread."""
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO company_alerts"
                "(company_key,stage,signal_id,slack_ts,alerted_at) VALUES(?,?,?,?,?)",
                (
                    company_key,
                    stage,
                    signal_id,
                    slack_ts,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def enqueue_alert(
        self,
        dedupe_key: str,
        kind: str,
        signal_id: int | None = None,
        official_company_id: int | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO alert_outbox(
                    dedupe_key, kind, signal_id, official_company_id, created_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    dedupe_key,
                    kind,
                    signal_id,
                    official_company_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def pending_alerts(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM alert_outbox WHERE sent_at IS NULL AND dead_at IS NULL "
                    "ORDER BY id LIMIT ?",
                    (limit,),
                )
            ]

    def mark_alert_sent(self, alert_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE alert_outbox SET sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), alert_id),
            )

    def mark_alert_error(self, alert_id: int, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE alert_outbox SET attempts=attempts+1, last_error=? WHERE id=?",
                (error[:1000], alert_id),
            )

    def mark_alert_dead(self, alert_id: int, error: str) -> None:
        """Retire an alert that can never succeed, so it stops blocking the queue."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE alert_outbox SET dead_at=?, last_error=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), error[:1000], alert_id),
            )

    def dead_alerts(self, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM alert_outbox WHERE dead_at IS NOT NULL "
                    "ORDER BY dead_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def outbox_stats(self) -> dict:
        with self.connect() as connection:
            def count(sql: str) -> int:
                return connection.execute(sql).fetchone()[0]

            return {
                "pending": count(
                    "SELECT COUNT(*) FROM alert_outbox WHERE sent_at IS NULL AND dead_at IS NULL"
                ),
                "sent": count("SELECT COUNT(*) FROM alert_outbox WHERE sent_at IS NOT NULL"),
                "dead": count("SELECT COUNT(*) FROM alert_outbox WHERE dead_at IS NOT NULL"),
            }

    def get_pond_run(self, run_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pond_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_pond_run(self, run_id: str, request_hash: str, response: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO pond_runs VALUES(?,?,?,?)",
                (
                    run_id,
                    request_hash,
                    json.dumps(response),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
