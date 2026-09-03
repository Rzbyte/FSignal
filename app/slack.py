"""Slack delivery -- the actual user interface of this product.

The alert has to answer four questions in the few seconds a GTM professional
gives it: which company, is this genuinely early, how do we know, and what do I
do next. The third one is the differentiator, so every EARLY alert carries a
receipt naming the corpus it was checked against and the moment it was checked.
A reader can verify the claim on ycombinator.com before replying to the founder.

DEMO_MODE writes the same Block Kit payloads to a local JSONL feed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import settings
from .intelligence import compact_evidence, compact_gtm_reasons

try:  # Pacific time matches how the task itself writes timestamps.
    from zoneinfo import ZoneInfo

    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - missing tzdata
    PACIFIC = timezone.utc


def _pacific(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    label = "PT" if PACIFIC is not timezone.utc else "UTC"
    return f"{moment.astimezone(PACIFIC):%b %-d, %Y, %-I:%M %p} {label}"


def _clock(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"{moment.astimezone(timezone.utc):%H:%M} UTC"


#: How each attempted match reads once it has failed.
_METHOD_LABELS = {
    "domain": "no domain match",
    "exact_name": "no exact-name match",
    "batch_prefix": "no in-batch prefix match",
}


def official_receipt(signal: dict) -> str:
    """The line that turns "not in the directory" from a claim into evidence."""
    try:
        check = json.loads(signal.get("official_check_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        check = {}
    if not check:
        return "Official-directory check: no record stored for this signal."

    size = check.get("snapshot_size") or check.get("records_checked") or 0
    source = "Speedrun" if check.get("snapshot_source") == "speedrun" else "YC"
    tried = [
        _METHOD_LABELS.get(method, f"no {method} match")
        for method in check.get("methods_tried") or []
    ]
    parts = [
        f"Checked against {size:,} {source} records",
        f"snapshot {_clock(check.get('snapshot_taken_at'))}",
    ]
    if check.get("batch_scope"):
        parts.append(f"batch scope {check['batch_scope']}")
    parts.extend(tried or ["no comparison performed"])
    return "🔎 " + " · ".join(parts)


class SlackNotifier:
    def __init__(self):
        # Derive the demo output directory from the configured DB path so the
        # file always lands in the same mounted volume as the database.
        self._data_dir = Path(settings.database_path).parent
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Alert types                                                         #
    # ------------------------------------------------------------------ #

    async def send_ghost(self, signal: dict):
        company = signal.get("company_name") or signal.get("company_domain") or "Unknown company"
        program = (signal.get("program") or "yc").upper()
        label = "SPEEDRUN" if program == "SPEEDRUN" else "YC"
        priority = (signal.get("gtm_priority") or "standard").upper()

        title = f"🔥 EARLY {label} SIGNAL · {company}"
        if signal.get("gtm_priority") == "high":
            title = f"🚨 {title}"

        fields = [
            f"*Company*\n{company}",
            f"*Founder / author*\n{signal.get('author_handle') or signal.get('author_name') or 'See post'}",
            f"*Program*\n{program}",
            f"*Batch*\n{signal.get('batch') or 'Not stated'}",
            f"*Source*\n{(signal.get('source') or '').title()}",
            f"*Detected*\n{_pacific(signal.get('detected_at'))}",
        ]

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": text[:2000]} for text in fields[:10]],
            },
        ]

        excerpt = (signal.get("text") or "").strip()
        if excerpt:
            quoted = "\n".join(f"> {line}" for line in excerpt[:600].splitlines() if line)
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": quoted[:3000]}})

        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": official_receipt(signal)[:3000]}],
            }
        )

        reasons = compact_gtm_reasons(signal)
        evidence = compact_evidence(signal)
        detail = []
        if reasons:
            detail.append("*Why act now*\n" + "\n".join(f"• {item}" for item in reasons))
        if evidence:
            detail.append("*Evidence*\n" + "\n".join(f"• {item}" for item in evidence))
        if detail:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(detail)[:3000]}}
            )

        links = [("Open founder post", signal.get("url"))]
        if signal.get("company_domain"):
            links.append(("Company website", f"https://{signal['company_domain']}"))
        blocks.append(self._actions(links))

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Signal confidence *{int(signal.get('confidence') or 0)}%* "
                            f"({(signal.get('confidence_label') or 'review').upper()}) · "
                            f"GTM priority *{int(signal.get('gtm_score') or 0)}/100* ({priority})"
                        ),
                    }
                ],
            }
        )
        return await self._send(title, blocks)

    async def send_corroboration(self, signal: dict, thread_ts: str | None):
        """A second independent source for a company already announced."""
        company = signal.get("company_name") or "this company"
        title = f"➕ Corroborating signal · {company}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{(signal.get('source') or '').title()}* also shows "
                        f"*{company}*"
                        f"{' (' + signal['batch'] + ')' if signal.get('batch') else ''}. "
                        "No new alert raised -- this strengthens the existing signal."
                    ),
                },
            },
            self._actions([("Open corroborating post", signal.get("url"))]),
        ]
        return await self._send(title, blocks, thread_ts=thread_ts)

    async def send_official(self, company: dict):
        is_yc = company.get("source") == "yc_directory"
        label = "YC" if is_yc else "SPEEDRUN"
        emoji = "✅" if is_yc else "🏁"
        company_name = company.get("name") or "Unknown"
        title = f"{emoji} NEW {label} COMPANY · {company_name}"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Company*\n{company_name}"},
                    {"type": "mrkdwn", "text": f"*Batch*\n{company.get('batch') or 'Unknown'}"},
                    {"type": "mrkdwn", "text": f"*Program*\n{label}"},
                    {"type": "mrkdwn", "text": "*Status*\nConfirmed by the official directory"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (company.get("description") or "New directory entry since the previous scan.")[:3000],
                },
            },
            self._actions([("Open official profile", company.get("url"))]),
        ]
        return await self._send(title, blocks)

    async def send_confirmed(self, signal: dict, company: dict):
        detected = datetime.fromisoformat(signal["detected_at"])
        confirmed = datetime.fromisoformat(signal["confirmed_at"])
        hours = (confirmed - detected).total_seconds() / 3600
        company_name = company.get("name") or signal.get("company_name") or "Unknown"
        title = f"✅ CONFIRMED · {company_name} · {hours:.1f}h early"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Company*\n{company_name}"},
                    {"type": "mrkdwn", "text": f"*Batch*\n{company.get('batch') or signal.get('batch') or 'Unknown'}"},
                    {"type": "mrkdwn", "text": f"*First seen on*\n{(signal.get('source') or '').title()}"},
                    {"type": "mrkdwn", "text": f"*Confirmed by*\n{company.get('source')}"},
                    {"type": "mrkdwn", "text": f"*Detected*\n{_pacific(signal.get('detected_at'))}"},
                    {"type": "mrkdwn", "text": f"*Listed*\n{_pacific(signal.get('confirmed_at'))}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"⏱ Measured lead time *{hours:.1f} hours* — how long FSignal "
                            "knew about this company before the official directory listed it."
                        ),
                    }
                ],
            },
            self._actions(
                [
                    ("Original founder post", signal.get("url")),
                    ("Official profile", company.get("url")),
                ]
            ),
        ]
        return await self._send(title, blocks)

    # ------------------------------------------------------------------ #
    # Transport                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _actions(links: list[tuple[str, str | None]]) -> dict:
        buttons = [
            {"type": "button", "text": {"type": "plain_text", "text": label[:75]}, "url": url}
            for label, url in links
            if url
        ][:5]
        return (
            {"type": "actions", "elements": buttons}
            if buttons
            else {"type": "context", "elements": [{"type": "mrkdwn", "text": "No link available."}]}
        )

    async def _send(self, title: str, blocks: list[dict], thread_ts: str | None = None):
        payload = {"text": title[:3000], "blocks": blocks}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        if settings.demo_mode:
            with open(self._data_dir / "demo_slack.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
            return {"ok": True, "demo": True}

        if not settings.slack_bot_token or not settings.slack_channel_id:
            raise RuntimeError("Slack is not configured: set SLACK_BOT_TOKEN and SLACK_CHANNEL_ID")

        payload["channel"] = settings.slack_channel_id
        headers = {
            "Authorization": f"Bearer {settings.slack_bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds, headers=headers
        ) as client:
            response = await client.post("https://slack.com/api/chat.postMessage", json=payload)
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError(f"Slack API error: {result.get('error', 'unknown_error')}")
            return result
