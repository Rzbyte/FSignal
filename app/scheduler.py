"""Per-source independent scheduler with adaptive exponential backoff.

Each monitored entity — YC Directory, Speedrun, X, LinkedIn, and ghost
reconciliation — runs in its own asyncio Task on a separate configurable
interval.  A failure (or missing credentials) in any single task is caught,
logged, and backed off; it never disrupts the remaining tasks.

Backoff policy
--------------
success                  → reset consecutive_failures, schedule at interval
not_configured (no creds)→ no failure counted, schedule at interval
waiting (no baseline yet)→ no failure counted, schedule at interval
error (1st)              → 1× interval
error (2nd)              → 2× interval
error (3rd)              → 4× interval
…                        → exponential, capped at MAX_BACKOFF_SECONDS (1 hour)

X / since_id limitation
-----------------------
The current XSource uses X API v2 recent-search without cursor tracking.
The database-level dedup on (source, external_id) ensures repeated polls do
not create duplicate signals or duplicate alerts. Adding a persistent since_id
watermark is left as a future improvement once API quotas are confirmed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scanner import Scanner

logger = logging.getLogger(__name__)

#: Hard cap on how long we wait before retrying a failing source.
MAX_BACKOFF_SECONDS: float = 3600.0  # 1 hour


@dataclass
class SourceState:
    """Runtime scheduling state for one monitored source."""

    name: str
    interval_seconds: float

    last_status: str | None = None
    last_run: datetime | None = None
    last_success: datetime | None = None
    next_run: datetime | None = None
    consecutive_failures: int = 0

    # ------------------------------------------------------------------ #
    # Backoff                                                              #
    # ------------------------------------------------------------------ #

    def backoff_seconds(self) -> float:
        """Return the seconds to wait before the next attempt.

        - 0 or 1 consecutive failures → normal interval (no penalty for a
          single transient error).
        - 2+ failures → doubles each time, capped at MAX_BACKOFF_SECONDS.
        """
        if self.consecutive_failures <= 1:
            return self.interval_seconds
        factor = min(2 ** (self.consecutive_failures - 1), 64)
        return min(self.interval_seconds * factor, MAX_BACKOFF_SECONDS)

    # ------------------------------------------------------------------ #
    # Health label                                                         #
    # ------------------------------------------------------------------ #

    @property
    def health_label(self) -> str:
        """Human-readable health bucket for /health and operator dashboards."""
        # A vendor billing block or a missing baseline is a real state worth
        # naming, not a generic failure -- "402 credits depleted" and "no snapshot
        # yet" need different responses from an operator.
        if self.last_status in {"billing_blocked", "not_configured", "waiting"}:
            return self.last_status
        if self.consecutive_failures >= 1:
            return "degraded"
        if self.last_run is None:
            # Task created but has not executed yet.
            return "pending"
        if self.last_success is None:
            return "not_configured"
        return "healthy"

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def as_dict(self) -> dict:
        """Return a JSON-serialisable snapshot of this source's state."""
        now = datetime.now(timezone.utc)
        seconds_until: int | None = None
        if self.next_run is not None:
            seconds_until = max(0, round((self.next_run - now).total_seconds()))
        return {
            "source": self.name,
            "health": self.health_label,
            "last_status": self.last_status,
            "interval_minutes": round(self.interval_seconds / 60, 1),
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "seconds_until_next": seconds_until,
            "consecutive_failures": self.consecutive_failures,
        }


class PerSourceScheduler:
    """Manages one asyncio.Task per monitored source.

    Each task runs its source on a configurable interval with exponential
    back-off on failure.  Tasks are independent — a crash in one does not
    cancel the others.

    Usage::

        scheduler = PerSourceScheduler.from_config(scanner)
        await scheduler.start()          # inside lifespan / startup
        ...
        await scheduler.stop()           # inside lifespan / shutdown
        scheduler.health()               # for /health endpoint
    """

    def __init__(self, scanner: Scanner, states: dict[str, SourceState]) -> None:
        self.scanner = scanner
        self.states = states
        self._tasks: list[asyncio.Task] = []
        self._stop: asyncio.Event | None = None
        self._startup_scan: bool = True

    # ------------------------------------------------------------------ #
    # Factory                                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(cls, scanner: Scanner) -> PerSourceScheduler:
        """Build a scheduler with intervals from the application settings."""
        from .config import settings

        def secs(minutes: float) -> float:
            return minutes * 60.0

        states: dict[str, SourceState] = {
            "yc_directory": SourceState(
                "yc_directory", secs(settings.yc_scan_interval_minutes)
            ),
            "speedrun": SourceState(
                "speedrun", secs(settings.speedrun_scan_interval_minutes)
            ),
            "x": SourceState(
                "x", secs(settings.x_scan_interval_minutes)
            ),
            "linkedin": SourceState(
                "linkedin", secs(settings.linkedin_scan_interval_minutes)
            ),
            "ghost_reconciliation": SourceState(
                "ghost_reconciliation", secs(settings.ghost_recheck_interval_minutes)
            ),
        }
        return cls(scanner, states)

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def start(self, startup_scan: bool = True) -> None:
        """Create and launch one asyncio.Task per source.

        Each task runs the source immediately (if *startup_scan* is True) and
        then on its own interval.  Returns as soon as the tasks are scheduled;
        they run concurrently in the background.
        """
        self._stop = asyncio.Event()
        self._startup_scan = startup_scan
        for state in self.states.values():
            task = asyncio.create_task(
                self._source_loop(state),
                name=f"radar:{state.name}",
            )
            self._tasks.append(task)

    async def stop(self) -> None:
        """Signal all source tasks to stop and wait for them to exit."""
        if self._stop is not None:
            self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------ #
    # Health                                                               #
    # ------------------------------------------------------------------ #

    def health(self) -> list[dict]:
        """Return a snapshot of all source states for /health."""
        return [state.as_dict() for state in self.states.values()]

    # ------------------------------------------------------------------ #
    # Private — task loop and execution                                   #
    # ------------------------------------------------------------------ #

    async def _source_loop(self, state: SourceState) -> None:
        """Run one source indefinitely with configurable interval and backoff.

        On startup: if *_startup_scan* is True the source runs immediately
        (next_run is None → 0-second wait).  If False we schedule the first
        run after one full interval.
        """
        assert self._stop is not None, "call start() before the loop runs"

        if not self._startup_scan:
            # Defer the first run by one interval.
            state.next_run = datetime.now(timezone.utc) + timedelta(
                seconds=state.interval_seconds
            )

        while not self._stop.is_set():
            # Determine how long to wait until the next scheduled run.
            now = datetime.now(timezone.utc)
            if state.next_run is None:
                # next_run=None signals "run immediately".
                wait_secs = 0.0
            else:
                wait_secs = max(0.0, (state.next_run - now).total_seconds())

            if wait_secs > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._stop.wait()), timeout=wait_secs
                    )
                    # Stop event fired during the wait.
                    return
                except asyncio.TimeoutError:
                    pass  # Time to run.

            if self._stop.is_set():
                return

            await self._run_once(state)

    async def _run_once(self, state: SourceState) -> None:
        """Execute one scan, update state, and schedule the next run."""
        state.last_run = datetime.now(timezone.utc)
        succeeded = False
        is_not_configured = False

        try:
            if state.name == "ghost_reconciliation":
                await self.scanner.run_reconciliation()
                succeeded = True
            else:
                result = await self.scanner.run_named_source(state.name)
                state.last_status = result["status"]
                if result["status"] == "ok":
                    succeeded = True
                elif result["status"] in {"not_configured", "waiting", "billing_blocked"}:
                    # Missing credentials, or waiting on the first official
                    # snapshot. Expected states, not faults: no failure counted
                    # and no back-off applied.
                    is_not_configured = True
                else:
                    logger.warning(
                        "Source %s returned error (consecutive=%d): %s",
                        state.name,
                        state.consecutive_failures + 1,
                        result.get("error", ""),
                    )

        except asyncio.CancelledError:
            # Propagate task cancellation cleanly.
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected error in scheduler task for %s: %s", state.name, exc
            )

        if succeeded:
            state.consecutive_failures = 0
            state.last_success = datetime.now(timezone.utc)
        elif not is_not_configured:
            state.consecutive_failures += 1

        # Schedule the next run using the (possibly backed-off) delay.
        backoff = state.backoff_seconds()
        state.next_run = datetime.now(timezone.utc) + timedelta(seconds=backoff)
