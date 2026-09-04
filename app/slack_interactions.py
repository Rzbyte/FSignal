"""Verify and read Slack interaction payloads.

Slack posts here whenever somebody touches a button on an alert. The alert
buttons are navigation links -- they open the founder's post, or the directory
search that backs the claim -- so there is no business logic to run on a click.
What matters is that the click is *acknowledged*: with no endpoint configured
Slack marks the message with a warning triangle, which on an alert whose whole
job is to look trustworthy is the worst possible place for one.

So this module is deliberately small. It authenticates the request and reports
what was clicked. It does not act on it.

Verification follows Slack's published scheme: HMAC-SHA256 over
``v0:{timestamp}:{raw body}``, compared in constant time, with a freshness
window that makes a captured request useless to replay.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

from .config import settings

#: Slack's signature version prefix. Bumped by Slack, not by us.
_VERSION = "v0"


class InteractionError(Exception):
    """A request that must be refused, carrying the status to refuse it with.

    ``reason`` is a stable machine code. It never contains any part of the
    signing secret, the signature, or the request body -- an error message is
    the easiest place to leak the thing you were checking.
    """

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def verify_signature(
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
    secret: str | None = None,
    max_age_seconds: int | None = None,
) -> None:
    """Raise unless this request really came from Slack, recently.

    Order matters. The cheap structural checks run before the HMAC so a flood of
    malformed requests cannot make us do crypto, and the freshness check runs
    before it too so a replayed-but-validly-signed capture is refused on age
    rather than accepted on signature.
    """
    signing_secret = settings.slack_signing_secret if secret is None else secret
    if not signing_secret:
        # Accepting unverified requests because the deployment forgot to
        # configure a secret is worse than answering nothing at all.
        raise InteractionError(503, "signing_secret_not_configured")

    if not timestamp or not signature:
        raise InteractionError(401, "missing_signature_headers")

    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        raise InteractionError(401, "malformed_timestamp") from None

    window = (
        settings.slack_interaction_max_age_seconds
        if max_age_seconds is None
        else max_age_seconds
    )
    # Both directions: a far-future timestamp is as much a forgery signal as a
    # stale one, and only checking the past leaves the window trivially wide.
    if abs((time.time() if now is None else now) - sent_at) > window:
        raise InteractionError(401, "stale_timestamp")

    expected = "{}={}".format(
        _VERSION,
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{_VERSION}:{sent_at}:".encode() + body,
            hashlib.sha256,
        ).hexdigest(),
    )
    if not hmac.compare_digest(expected, signature):
        raise InteractionError(401, "bad_signature")


def parse_payload(body: bytes) -> dict:
    """Read Slack's form-encoded ``payload=<json>`` body.

    Every failure here is the same answer to the caller -- a malformed body is
    malformed however it is malformed -- so the shapes are collapsed into one
    reason rather than reported in detail back to whoever sent it.
    """
    try:
        fields = parse_qs(body.decode("utf-8"), strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise InteractionError(400, "malformed_body") from None

    raw = (fields.get("payload") or [None])[0]
    if not raw:
        raise InteractionError(400, "missing_payload")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise InteractionError(400, "malformed_payload") from None

    if not isinstance(payload, dict):
        raise InteractionError(400, "malformed_payload")
    return payload


def describe(payload: dict) -> str:
    """A one-line, log-safe summary of what was clicked.

    Only the interaction's own shape is read: its type, and the label or id of
    the element touched. No user identity, no channel, no message text, nothing
    from the request headers.
    """
    kind = payload.get("type") or "unknown"
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return kind

    first = actions[0] if isinstance(actions[0], dict) else {}
    text = first.get("text")
    label = text.get("text") if isinstance(text, dict) else None
    name = label or first.get("action_id") or "unnamed"
    return f"{kind}:{str(name)[:60]}"
