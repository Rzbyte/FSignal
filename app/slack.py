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
from .presenter import (
    company_display,
    display_evidence,
    display_reasons,
    excerpt,
    founder_display,
    official_batch_label,
    official_check_lines,
    program_batch,
    program_label,
    source_badge,
    source_display,
    verify_url,
)

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


#: What to actually do about the handful of Slack errors a new operator hits.
#: Without this the first run of a correctly-written bot fails with the bare
#: string "not_in_channel", which appears nowhere in the docs and reads like a
#: defect rather than a missing /invite.
_SLACK_REMEDIES = {
    "not_in_channel": (
        "the bot is not in that channel. In Slack, open the channel and run "
        "`/invite @FSignal`."
    ),
    "channel_not_found": (
        "SLACK_CHANNEL_ID does not match a channel this bot can see. Copy the ID "
        "from the bottom of the channel's About tab (it starts with C), or a DM "
        "ID (starts with D)."
    ),
    "invalid_auth": (
        "SLACK_BOT_TOKEN is wrong or was revoked. Copy the Bot User OAuth Token "
        "(xoxb-...) from your app's OAuth & Permissions page."
    ),
    "token_revoked": (
        "SLACK_BOT_TOKEN has been revoked. Reinstall the app to the workspace "
        "and copy the new Bot User OAuth Token."
    ),
    "account_inactive": "the bot user is deactivated in this workspace.",
    "missing_scope": (
        "the app lacks the chat:write scope. Add it under OAuth & Permissions, "
        "then click Reinstall to Workspace -- a scope added without reinstalling "
        "does not take effect."
    ),
    "is_archived": "that channel is archived. Unarchive it or pick another.",
    "ratelimited": "Slack is rate-limiting us; the outbox will retry on its own.",
}


def _slack_remedy(code: str) -> str:
    remedy = _SLACK_REMEDIES.get(code)
    return f" -- {remedy}" if remedy else ""


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


#: The only symbols in the product. Each one means a state, not decoration.
EARLY = "\U0001f525"      # a founder announced ahead of the directory
CONFIRMED = "\u2705"      # the directory caught up
CHECK = "\U0001f50e"      # independent verification against the official source
STATE = "\u26a1"          # the one-line status assertion


def _lead_time(detected, confirmed) -> str:
    """"47h 18m" / "3d 4h" -- only ever from real persisted timestamps."""
    seconds = max(0, (confirmed - detected).total_seconds())
    hours, minutes = divmod(int(seconds // 60), 60)
    if hours >= 48:
        days, rem = divmod(hours, 24)
        return f"{days}d {rem}h"
    return f"{hours}h {minutes:02d}m"


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
        """The hero alert: a founder announced before the directory caught up.

        The block order is the reading order a GTM user needs -- who, from where,
        what state, in their own words, then the independent verification, then
        what to do about it.
        """
        company = signal.get("company_name") or "Unknown company"
        label = program_label(signal)
        title = f"{EARLY} EARLY {label} SIGNAL · {company}"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            self._fields(
                [
                    ("Company", company_display(signal)),
                    ("Founder", founder_display(signal)),
                    ("Program", label),
                    ("Batch", signal.get("batch") or "Not stated"),
                    ("Source", source_display(signal)),
                    ("Detected", _pacific(signal.get("detected_at"))),
                ]
            ),
            self._context(source_badge(signal)),
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Status*\n{STATE} Founder announced · "
                        f"not yet listed in {program_batch(signal)}"
                    ),
                },
            },
        ]

        quote = excerpt(signal)
        if quote:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": f"> {quote}"[:3000]}}
            )

        blocks.append({"type": "divider"})
        headline, provenance, detail = official_check_lines(signal)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{CHECK} *OFFICIAL CHECK*\n{headline}\n{provenance}"[:3000],
                },
            }
        )
        if detail:
            blocks.append(self._context(detail))

        blocks.extend(self._bullets("Why act now", display_reasons(signal, 3)))
        blocks.extend(self._bullets("Evidence", display_evidence(signal, 3)))

        blocks.append(
            self._actions(
                [
                    ("View founder post  →", signal.get("url"), "primary"),
                    (f"Verify {label} status  →", verify_url(signal), None),
                ]
            )
        )
        blocks.append(self._context(self._scoreline(signal)))
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
                        f"*{source_display(signal)}* also shows "
                        f"*{company}*"
                        f"{' (' + signal['batch'] + ')' if signal.get('batch') else ''}. "
                        "No new alert raised -- this strengthens the existing signal."
                    ),
                },
            },
            self._context(source_badge(signal)),
            self._actions([("View corroborating post  →", signal.get("url"), None)]),
        ]
        return await self._send(title, blocks, thread_ts=thread_ts)

    async def send_official(self, company: dict):
        """A directory addition FSignal did not see announced beforehand."""
        is_yc = company.get("source") == "yc_directory"
        label = "YC" if is_yc else "SPEEDRUN"
        # The header shouts; the status line is read as a sentence.
        program = "YC" if is_yc else "Speedrun"
        company_name = company.get("name") or "Unknown"
        title = f"{CONFIRMED} NEW {label} COMPANY · {company_name}"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            self._fields(
                [
                    ("Company", company_name),
                    ("Batch", company.get("batch") or "Unknown"),
                    ("Program", label),
                    ("Status", f"{CONFIRMED} Confirmed by {program}"),
                    # When the row first entered our snapshot -- which is when we
                    # could first have told anybody, not when the directory
                    # published it.
                    ("Detected", _pacific(company.get("first_seen_at"))),
                ]
            ),
            self._context(f"`SOURCE · {label} DIRECTORY`"),
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (company.get("description")
                             or "New directory entry since the previous scan.")[:3000],
                },
            },
            self._actions([(f"View {label} profile  →", company.get("url"), "primary")]),
        ]
        return await self._send(title, blocks)

    async def send_confirmed(self, signal: dict, company: dict):
        """The receipt for the whole thesis: we saw it first, by this much."""
        detected = datetime.fromisoformat(signal["detected_at"])
        confirmed = datetime.fromisoformat(signal["confirmed_at"])
        lead = _lead_time(detected, confirmed)

        company_name = company.get("name") or signal.get("company_name") or "Unknown"
        label = program_label(signal)
        batch_label = company.get("batch") or official_batch_label(signal.get("batch"))
        title = f"{CONFIRMED} {label} CONFIRMED · {company_name}"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Status*\n{CONFIRMED} Officially listed"
                        f"{' in ' + label + ' ' + batch_label if batch_label else ''}"
                    ),
                },
            },
            self._fields(
                [
                    ("Company", company_name),
                    ("Founder", founder_display(signal)),
                    ("Program", label),
                    ("Batch", batch_label or "Unknown"),
                    ("Early detected", _pacific(signal.get("detected_at"))),
                    ("Officially listed", _pacific(signal.get("confirmed_at"))),
                ]
            ),
            self._context(source_badge(signal)),
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*EARLY DETECTED  →  OFFICIALLY CONFIRMED*\n"
                        f"Early detection lead: *{lead}* before official listing"
                    ),
                },
            },
            self._actions(
                [
                    (f"View {label} profile  →", company.get("url"), "primary"),
                    ("View original announcement  →", signal.get("url"), None),
                ]
            ),
            self._context(self._scoreline(signal)),
        ]
        return await self._send(title, blocks)

    # ------------------------------------------------------------------ #
    # Transport                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fields(pairs: list[tuple[str, str]]) -> dict:
        """Two-column identity block. Slack caps this at 10 fields, 2000 chars each."""
        return {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{name}*\n{value}"[:2000]}
                for name, value in pairs[:10]
            ],
        }

    @staticmethod
    def _context(text: str) -> dict:
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}

    @staticmethod
    def _bullets(heading: str, items: list[str]) -> list[dict]:
        if not items:
            return []
        body = "\n".join(f"• {item}" for item in items)
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{heading}*\n{body}"[:3000]}}
        ]

    @staticmethod
    def _scoreline(signal: dict) -> str:
        """Two different questions, kept visibly separate and never inflated."""
        confidence = int(signal.get("confidence") or 0)
        gtm = int(signal.get("gtm_score") or 0)
        return (
            f"Confidence: *{confidence}%* · {(signal.get('confidence_label') or 'review').title()}"
            f"    ·    GTM priority: *{gtm}/100* · "
            f"{(signal.get('gtm_priority') or 'standard').title()}"
        )

    @staticmethod
    def _actions(links: list[tuple[str, str | None, str | None]]) -> dict:
        """At most two navigation actions: inspect the founder, verify the claim."""
        buttons = []
        for label, url, style in links:
            if not url:
                continue
            button = {"type": "button", "text": {"type": "plain_text", "text": label[:75]}, "url": url}
            if style:
                button["style"] = style
            buttons.append(button)
        return (
            {"type": "actions", "elements": buttons[:2]}
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
                code = result.get("error", "unknown_error")
                raise RuntimeError(f"Slack API error: {code}{_slack_remedy(code)}")
            return result
