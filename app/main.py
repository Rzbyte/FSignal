"""FastAPI service, per-source scheduler, health UI, and Pond Protocol endpoints."""

from __future__ import annotations

import asyncio
import html
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from .config import settings
from .db import Database
from .pond import (
    PondProtocolError,
    PondRunRequest,
    authenticate,
    error_payload,
    failed_terminal,
    manifest,
    request_hash,
    terminal,
    validate_parameters,
)
from .scanner import Scanner
from .scheduler import PerSourceScheduler

db = Database(settings.database_path)
scanner = Scanner(db)
per_source_scheduler = PerSourceScheduler.from_config(scanner)
pond_run_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the per-source scheduler on boot; stop it cleanly on shutdown."""
    await per_source_scheduler.start(startup_scan=settings.startup_scan)
    try:
        yield
    finally:
        await per_source_scheduler.stop()


app = FastAPI(title="FSignal", version="1.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    """Machine-readable source health, scheduler state, and deployment readiness.

    Each source exposes:
      health           healthy | not_configured | degraded | pending
      interval_minutes configured polling frequency
      last_run         ISO timestamp of last attempt (null if never started)
      last_success     ISO timestamp of last successful scan
      next_run         ISO timestamp of scheduled next attempt
      seconds_until_next   seconds until next attempt (0 = due now)
      consecutive_failures count of consecutive errors (0 = healthy)

    production_ready becomes true when demo_mode is off and all four
    monitoring sources (x, linkedin, yc_directory, speedrun) are healthy.
    """
    sources = per_source_scheduler.health()
    monitoring_sources = [s for s in sources if s["source"] != "ghost_reconciliation"]
    production_ready = not settings.demo_mode and all(
        s["health"] == "healthy" for s in monitoring_sources
    )
    return {
        "ok": True,
        "production_ready": production_ready,
        "demo_mode": settings.demo_mode,
        # What each EARLY verdict was adjudicated against, and what the bot
        # decided about everything it looked at.
        "snapshots": db.snapshots(),
        "ledger": db.ledger_summary(),
        "polling": {
            "x_minutes": settings.x_scan_interval_minutes,
            "linkedin_minutes": settings.linkedin_scan_interval_minutes,
            "yc_directory_minutes": settings.yc_scan_interval_minutes,
            "speedrun_minutes": settings.speedrun_scan_interval_minutes,
            "ghost_recheck_minutes": settings.ghost_recheck_interval_minutes,
        },
        "stats": db.stats(),
        "alerts": db.outbox_stats(),
        "sources": sources,
    }


#: How a collection path reads on the dashboard.
_MODE_LABELS = {
    "native": "platform API",
    "indexed_fallback": "indexed search",
    "full": "full crawl",
    "hot": "recent window",
    "canonical": "first-party API",
    "fallback": "fallback scrape",
}


def _is_handle_name(signal: dict) -> bool:
    """True when the company is named by its social handle rather than its name."""
    return (signal.get("company_name") or "").strip().startswith("@")


def _source_label(signal: dict) -> str:
    """`X` or `X (indexed search)` -- never the bare platform when it was not."""
    name = (signal.get("source") or "").replace("linkedin", "LinkedIn").upper()
    name = "LinkedIn" if name == "LINKEDIN" else name
    if signal.get("collection_mode") == "indexed_fallback":
        return f"{name} (indexed search)"
    return name


@app.get("/", response_class=HTMLResponse)
def home():
    stats = db.stats()
    sched_sources = per_source_scheduler.health()
    rows = (
        "".join(
            "<tr>"
            f"<td>{html.escape(item['source'])}</td>"
            f"<td>{html.escape(item.get('health') or 'unknown')}</td>"
            # Which path answered. An indexed result must never read as a native one,
            # and this page is where most people look first.
            f"<td>{html.escape(_MODE_LABELS.get(item.get('mode'), '—'))}</td>"
            f"<td>{html.escape(str(item.get('interval_minutes') or '—'))}</td>"
            f"<td>{html.escape(item.get('last_run') or '—')}</td>"
            f"<td>{html.escape(item.get('next_run') or '—')}</td>"
            f"<td>{item.get('consecutive_failures', 0)}</td>"
            "</tr>"
            for item in sched_sources
        )
        or '<tr><td colspan="7">Scheduler not started.</td></tr>'
    )

    # The actual findings, not just counts. Someone landing here should see what
    # the bot found before they see how it is configured.
    #
    # One row per company, not per post. `list_ghosts` returns signals, and a
    # company with corroborating posts has several -- which on a page headed
    # "open early signals" reads as several separate discoveries.
    #
    # Which of them to show is not simply the first. The list is sorted by score,
    # and the highest-scoring signal for Adalat AI named the company `@Adalat_AI`
    # -- a real identity, and the worst of the three available ways to write it.
    # A signal that names the company properly wins the row.
    by_company: dict[str, dict] = {}
    for candidate in db.list_ghosts(50):
        key = candidate.get("company_key") or f"id:{candidate['id']}"
        held = by_company.get(key)
        if held is None or (_is_handle_name(held) and not _is_handle_name(candidate)):
            by_company[key] = candidate
    ghosts = list(by_company.values())[:10]
    ghost_rows = (
        "".join(
            "<tr>"
            f"<td><b>{html.escape(g.get('company_name') or 'Unknown')}</b></td>"
            f"<td>{html.escape(g.get('batch') or '—')}</td>"
            f"<td>{html.escape((g.get('program') or '').upper())}</td>"
            f"<td>{html.escape(_source_label(g))}</td>"
            f"<td>{g.get('confidence') or 0}%</td>"
            f'<td><a href="{html.escape(g.get("url") or "#")}">post</a> · '
            f'<a href="/signals/{g["id"]}/timeline">timeline</a></td>'
            "</tr>"
            for g in ghosts
        )
        or '<tr><td colspan="6">No open early signals right now.</td></tr>'
    )

    ledger = db.ledger_summary()
    ledger_rows = (
        "".join(
            f"<tr><td>{html.escape(str(label))}</td><td>{count}</td></tr>"
            for label, count in list(ledger["verdicts"].items())
            + list(ledger["reasons"].items())
        )
        or '<tr><td colspan="2">Nothing evaluated yet.</td></tr>'
    )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>FSignal</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
body {{
    font-family: 'Inter', system-ui, sans-serif;
    background: radial-gradient(circle at top, #141723 0%, #0a0c10 100%);
    color: #e2e8f0; padding: 40px; margin: 0; min-height: 100vh; line-height: 1.6;
}}
main {{ max-width: 1100px; margin: auto; }}
h1 {{
    font-size: 52px; font-weight: 800; margin-bottom: 8px;
    background: linear-gradient(135deg, #ff6b6b 0%, #ff8e53 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: -1px;
}}
p {{ color: #94a3b8; font-size: 16px; margin-bottom: 32px; }}
h2 {{ font-size: 24px; font-weight: 600; margin-top: 56px; color: #f8fafc; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 12px; }}
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }}
.c {{
    background: rgba(255, 255, 255, 0.03); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 20px; padding: 24px;
    box-shadow: 0 4px 24px -4px rgba(0,0,0,0.3); transition: transform 0.2s ease, background 0.2s ease, border-color 0.2s ease;
}}
.c:hover {{
    transform: translateY(-4px); background: rgba(255, 255, 255, 0.05); border-color: rgba(255, 142, 83, 0.4);
}}
.n {{ font-size: 42px; font-weight: 800; color: #fff; line-height: 1.1; margin-bottom: 4px; }}
table {{ width: 100%; margin-top: 24px; border-collapse: separate; border-spacing: 0; }}
th {{ padding: 16px; text-align: left; font-weight: 600; color: #64748b; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; border-bottom: 1px solid rgba(255,255,255,0.08); }}
td {{ padding: 16px; border-bottom: 1px solid rgba(255,255,255,0.04); color: #cbd5e1; }}
tr {{ transition: background 0.15s ease; }}
tr:hover td {{ background: rgba(255,255,255,0.02); }}
a {{ color: #60a5fa; text-decoration: none; transition: color 0.15s, text-shadow 0.15s; }}
a:hover {{ color: #93c5fd; text-shadow: 0 0 12px rgba(96, 165, 250, 0.4); }}
b {{ color: #f8fafc; }}
</style>
<main>
<h1>FSignal</h1>
<p>Founder-announced signals before official directory confirmation.</p>
<div class="cards">
<div class="c"><div class="n">{stats['ghosts']}</div>Ghosts</div>
<div class="c"><div class="n">{stats['confirmed']}</div>Confirmed</div>
<div class="c"><div class="n">{stats['signals']}</div>Signals</div>
<div class="c"><div class="n">{stats['high_priority_ghosts']}</div>High priority</div>
</div>
<h2>Open early signals</h2>
<p>Announced by a founder, not yet listed in the official directory. Every one links to
the post it came from and to its full history.</p>
<table><tr><th>Company</th><th>Batch</th><th>Program</th><th>Source</th><th>Confidence</th><th>Evidence</th></tr>{ghost_rows}</table>
<h2>Source health &amp; scheduler</h2>
<table><tr><th>Source</th><th>Health</th><th>Path</th><th>Interval (min)</th><th>Last run</th><th>Next run</th><th>Failures</th></tr>{rows}</table>
<p>Average proven early lead: <b>{stats['average_early_lead_hours'] if stats['average_early_lead_hours'] is not None else '—'} hours</b></p>
<h2>Suppression ledger</h2>
<p>Every candidate evaluated, and why it did or did not become an alert.</p>
<table><tr><th>Verdict / reason</th><th>Count</th></tr>{ledger_rows}</table>
<p><a href="/ledger">Full ledger</a> · <a href="/demo/slack">Demo Slack feed</a> · <a href="/manifest">Pond manifest</a> · <a href="/health">JSON health</a></p>
</main>"""


@app.get("/demo/slack", response_class=HTMLResponse)
def demo_slack():
    path = Path(settings.database_path).parent / "demo_slack.jsonl"
    items = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    cards = []
    for item in reversed(items):
        chunks = []
        for block in item.get("blocks", []):
            if block.get("type") == "section":
                chunks.append(block.get("text", {}).get("text", ""))
            if block.get("type") == "context":
                chunks.extend(
                    element.get("text", "") for element in block.get("elements", [])
                )
        body = html.escape("\n".join(chunks)).replace("*", "").replace("\n", "<br>")
        cards.append(
            '<div class="m"><div class="a">G</div><div>'
            "<b>FSignal</b> <small>APP</small>"
            f"<h3>{html.escape(item.get('text', 'Alert'))}</h3>{body}</div></div>"
        )

    return (
        '<!doctype html><meta charset="utf-8"><style>'
        "body{font-family:Inter,system-ui;margin:0}header{background:#3f0e40;color:white;padding:16px 28px}"
        "main{max-width:900px;margin:auto}.m{display:grid;grid-template-columns:46px 1fr;gap:10px;padding:20px;border-bottom:1px solid #eee}"
        ".a{width:38px;height:38px;background:#111;color:white;border-radius:8px;display:grid;place-items:center;font-weight:800}"
        "small{background:#eee;padding:2px 4px}</style><header># fsignal</header><main>"
        + (
            "".join(cards)
            or "<p>No demo alerts. Run python scripts/replay_corpus.py</p>"
        )
        + "</main>"
    )


@app.get("/ledger")
def ledger(limit: int = 100):
    """Every candidate evaluated, with the reason it was or was not alerted.

    This is what makes the precision claim checkable rather than asserted: a
    reviewer can see the candidates that were rejected next to the ones that
    produced an alert.
    """
    return {
        "summary": db.ledger_summary(),
        "candidates": db.recent_candidates(min(max(limit, 1), 500)),
    }


@app.get("/signals/{signal_id}/timeline")
def signal_timeline(signal_id: int):
    """Auditable lifecycle for one social discovery."""
    signal = db.get_signal(signal_id)
    if not signal:
        return JSONResponse(status_code=404, content={"error": "signal_not_found"})
    return {"signal": signal, "timeline": db.timeline(signal_id)}


@app.get("/manifest")
def get_manifest():
    return manifest()


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    """Pond Protocol V1 async task polling endpoint.

    FSignal operates synchronously (capabilities.async_tasks = false), so all
    runs return a terminal result directly from POST /runs. This endpoint exists
    solely to satisfy Pond's connectivity check during agent registration.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "task_not_found",
                "message": (
                    "This agent operates synchronously. All results are returned "
                    "directly from POST /runs. Async task polling is not used."
                ),
            }
        },
    )


@app.post("/runs")
async def runs(
    request: Request,
    authorization: str | None = Header(None),
    protocol_version: str | None = Header(None, alias="X-Agent-Protocol-Version"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Execute one prepared Pond Protocol V1 request synchronously."""
    run_id = None
    try:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise PondProtocolError(
                415,
                "unsupported_content_type",
                "POST /runs requires application/json.",
            )

        authenticate(authorization, protocol_version)

        raw_body = await request.body()
        if len(raw_body) > manifest()["limits"]["max_request_bytes"]:
            raise PondProtocolError(
                400, "invalid_request", "The request exceeds the configured size limit."
            )

        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PondProtocolError(
                400, "invalid_request", "The request body must be valid JSON."
            )
        if not isinstance(body, dict):
            raise PondProtocolError(
                400, "invalid_request", "The request body must be a JSON object."
            )

        # Validate the complete prepared-request envelope, not just action parameters.
        try:
            run = PondRunRequest.model_validate(body)
        except ValidationError:
            raise PondProtocolError(
                400,
                "invalid_request",
                "The request does not match Pond Protocol V1.",
            )

        run_id = run.run_id
        if idempotency_key != run_id:
            raise PondProtocolError(
                400,
                "invalid_request",
                "Idempotency-Key must exactly match run_id.",
                run_id=run_id,
            )

        parameters = validate_parameters(run.action_id, run.parameters)
        body_hash = request_hash(body)

        # Coalesce concurrent duplicate requests in this server process. Completed
        # responses are also persisted in SQLite and survive process restarts.
        async with pond_run_locks[run_id]:
            previous = db.get_pond_run(run_id)
            if previous:
                if previous["request_hash"] != body_hash:
                    raise PondProtocolError(
                        409,
                        "idempotency_conflict",
                        "The run_id was already used with a different request.",
                        run_id=run_id,
                    )
                return json.loads(previous["response_json"])

            try:
                if run.action_id == "scan_now":
                    # Manual full scan — runs all sources sequentially and is
                    # independent of the background per-source scheduler.
                    scan_result = await asyncio.wait_for(
                        scanner.scan_all(),
                        timeout=run.execution.deadline_ms / 1000,
                    )
                    result = terminal(
                        run_id,
                        "Scan completed.\n```json\n"
                        + json.dumps(scan_result, indent=2)
                        + "\n```",
                    )
                elif run.action_id == "get_status":
                    result = terminal(
                        run_id,
                        "```json\n"
                        + json.dumps(
                            {
                                "stats": db.stats(),
                                "alerts": db.outbox_stats(),
                                "snapshots": db.snapshots(),
                                "ledger": db.ledger_summary(),
                                "scheduler": per_source_scheduler.health(),
                            },
                            indent=2,
                        )
                        + "\n```",
                    )
                elif run.action_id == "list_ghosts":
                    ghosts = db.list_ghosts(parameters["limit"])
                    text = (
                        "\n".join(
                            f"- #{ghost['id']} {ghost.get('company_name') or ghost.get('company_domain')} — "
                            f"{ghost.get('source')} — confidence {ghost.get('confidence') or 0}% — "
                            f"GTM {ghost.get('gtm_score') or 0}/100 ({ghost.get('gtm_priority') or 'standard'}) — "
                            f"{ghost.get('url')}"
                            for ghost in ghosts
                        )
                        or "No current ghost signals."
                    )
                    result = terminal(run_id, text)
                else:  # get_timeline
                    signal = db.get_signal(parameters["signal_id"])
                    if not signal:
                        raise PondProtocolError(
                            422, "invalid_input", "signal_id was not found."
                        )
                    result = terminal(
                        run_id,
                        "```json\n"
                        + json.dumps(
                            {
                                "signal": signal,
                                "timeline": db.timeline(parameters["signal_id"]),
                            },
                            indent=2,
                        )
                        + "\n```",
                    )
            except Exception:
                # Execution has already been accepted. Pond V1 expects a terminal
                # failed result instead of leaking an infrastructure exception.
                result = failed_terminal(
                    run_id,
                    "FSignal could not complete this execution.",
                )

            db.save_pond_run(run_id, body_hash, result)
            return result

    except PondProtocolError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc, run_id=run_id),
        )
