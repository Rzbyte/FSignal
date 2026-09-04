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
            f"""<td><span class="badge {'healthy' if item.get('health') == 'healthy' else 'error'}">{html.escape(item.get('health') or 'unknown')}</span></td>"""
            # Which path answered. An indexed result must never read as a native one,
            # and this page is where most people look first.
            f"<td>{html.escape(_MODE_LABELS.get(item.get('mode'), '—'))}</td>"
            f"<td>{html.escape(str(item.get('interval_minutes') or '—'))}</td>"
            f"<td>{html.escape((item.get('last_run') or '—')[:16].replace('T', ' '))}</td>"
            f"<td>{html.escape((item.get('next_run') or '—')[:16].replace('T', ' '))}</td>"
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
<html lang="en">
<head>
<meta charset="utf-8">
<title>FSignal - Premium Radar</title>
<script src="https://unpkg.com/@phosphor-icons/web"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@400;500&display=swap');
:root {{
    --bg-main: #09090b; --bg-card: rgba(255, 255, 255, 0.03); --bg-card-hover: rgba(255, 255, 255, 0.06);
    --border: rgba(255, 255, 255, 0.08); --border-hover: rgba(249, 115, 22, 0.4);
    --text-primary: #f8fafc; --text-secondary: #94a3b8;
    --brand: #f97316; --brand-grad: linear-gradient(135deg, #f97316 0%, #ef4444 100%);
}}
* {{ box-sizing: border-box; }}
body {{
    font-family: 'Inter', sans-serif; background: var(--bg-main);
    color: var(--text-primary); margin: 0; min-height: 100vh; line-height: 1.6;
    background-image: radial-gradient(circle at 50% 0%, rgba(249, 115, 22, 0.08) 0%, transparent 50%);
}}
nav {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px 40px; border-bottom: 1px solid var(--border);
    background: rgba(9, 9, 11, 0.7); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    position: sticky; top: 0; z-index: 10;
}}
.brand {{ font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 800; display: flex; align-items: center; gap: 8px; }}
.brand i {{ color: var(--brand); font-size: 28px; }}
.nav-links a {{ color: var(--text-secondary); text-decoration: none; margin-left: 24px; font-size: 14px; font-weight: 500; transition: color 0.2s; }}
.nav-links a:hover {{ color: var(--text-primary); }}
main {{ max-width: 1200px; margin: 0 auto; padding: 48px 24px; }}
.hero {{ margin-bottom: 48px; }}
h1 {{ font-family: 'Outfit', sans-serif; font-size: 48px; font-weight: 800; margin: 0 0 16px; letter-spacing: -1px; }}
.hero p {{ color: var(--text-secondary); font-size: 18px; margin: 0; max-width: 600px; }}
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px; margin-bottom: 56px; }}
.c {{
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px; padding: 24px;
    transition: all 0.2s ease; position: relative; overflow: hidden;
}}
.c:hover {{ transform: translateY(-4px); background: var(--bg-card-hover); border-color: var(--border-hover); box-shadow: 0 8px 30px rgba(0,0,0,0.5); }}
.c-icon {{ font-size: 24px; color: var(--brand); margin-bottom: 16px; display: block; }}
.n {{ font-family: 'Outfit', sans-serif; font-size: 42px; font-weight: 800; line-height: 1; margin-bottom: 8px; }}
.c-label {{ color: var(--text-secondary); font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; }}
.section-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; margin-top: 64px; }}
h2 {{ font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 600; margin: 0; display: flex; align-items: center; gap: 8px; }}
.table-container {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
table {{ width: 100%; border-collapse: collapse; text-align: left; }}
th {{ padding: 16px 24px; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid var(--border); background: rgba(0,0,0,0.2); }}
td {{ padding: 16px 24px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.03); }}
tr:last-child td {{ border-bottom: none; }}
tr {{ transition: background 0.15s ease; }}
tr:hover td {{ background: rgba(255,255,255,0.02); }}
.badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; background: rgba(255,255,255,0.1); }}
.badge.healthy {{ background: rgba(34, 197, 94, 0.1); color: #4ade80; border: 1px solid rgba(34,197,94,0.2); }}
.badge.healthy::before {{ content: ''; display: block; width: 6px; height: 6px; border-radius: 50%; background: #4ade80; box-shadow: 0 0 8px #4ade80; }}
.badge.error {{ background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239,68,68,0.2); }}
a {{ color: #60a5fa; text-decoration: none; transition: all 0.2s; }}
a:hover {{ color: #93c5fd; text-decoration: underline; }}
.action-link {{ font-size: 14px; color: var(--brand); font-weight: 500; display: flex; align-items: center; gap: 4px; }}
.action-link:hover {{ color: #ef4444; text-decoration: none; }}
</style>
</head>
<body>
<nav>
    <div class="brand"><i class="ph-fill ph-radar"></i> FSignal</div>
    <div class="nav-links">
        <a href="/ledger"><i class="ph ph-book-open"></i> Ledger</a>
        <a href="/demo/slack"><i class="ph ph-slack-logo"></i> Slack Demo</a>
        <a href="/manifest"><i class="ph ph-file-code"></i> Pond</a>
        <a href="/health"><i class="ph ph-heartbeat"></i> Health</a>
    </div>
</nav>
<main>
    <div class="hero">
        <h1>Early Signal Radar</h1>
        <p>Founder-announced signals detected and verified before official directory confirmation.</p>
    </div>
    
    <div class="cards">
        <div class="c">
            <i class="ph ph-ghost c-icon"></i>
            <div class="n">{stats['ghosts']}</div>
            <div class="c-label">Open Ghosts</div>
        </div>
        <div class="c">
            <i class="ph ph-check-circle c-icon"></i>
            <div class="n">{stats['confirmed']}</div>
            <div class="c-label">Confirmed</div>
        </div>
        <div class="c">
            <i class="ph ph-broadcast c-icon"></i>
            <div class="n">{stats['signals']}</div>
            <div class="c-label">Total Signals</div>
        </div>
        <div class="c">
            <i class="ph ph-flame c-icon"></i>
            <div class="n">{stats['high_priority_ghosts']}</div>
            <div class="c-label">High Priority</div>
        </div>
    </div>

    <div class="section-header">
        <h2><i class="ph ph-magnifying-glass"></i> Open Early Signals</h2>
        <span class="action-link">Avg. lead time: {stats['average_early_lead_hours'] if stats['average_early_lead_hours'] is not None else '—'}h</span>
    </div>
    <div class="table-container">
        <table>
            <thead><tr><th>Company</th><th>Batch</th><th>Program</th><th>Source</th><th>Confidence</th><th>Evidence</th></tr></thead>
            <tbody>{ghost_rows}</tbody>
        </table>
    </div>

    <div class="section-header">
        <h2><i class="ph ph-activity"></i> Source Health & Scheduler</h2>
    </div>
    <div class="table-container">
        <table>
            <thead><tr><th>Source</th><th>Status</th><th>Path</th><th>Interval (min)</th><th>Last Run</th><th>Next Run</th><th>Failures</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>

    <div class="section-header">
        <h2><i class="ph ph-archive"></i> Suppression Ledger</h2>
    </div>
    <div class="table-container">
        <table>
            <thead><tr><th>Verdict / Reason</th><th>Count</th></tr></thead>
            <tbody>{ledger_rows}</tbody>
        </table>
    </div>
</main>
</body>
</html>"""


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
