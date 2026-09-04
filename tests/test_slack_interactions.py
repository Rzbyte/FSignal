"""Slack interaction authentication.

The endpoint runs no business logic, so what is worth testing is exactly the
part that decides whether a request is Slack's: the signature, the freshness
window, and that nothing it refuses tells the caller why in a way that helps
them forge the next one.
"""

import hashlib
import hmac
import json
import time
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.main import app
from app.slack_interactions import (
    InteractionError,
    describe,
    parse_payload,
    verify_signature,
)

SECRET = "test-signing-secret"

#: A real block_actions payload for one of the alert's navigation buttons.
BUTTON_CLICK = {
    "type": "block_actions",
    "user": {"id": "U123", "username": "reviewer"},
    "channel": {"id": "C123"},
    "actions": [
        {
            "type": "button",
            "action_id": "view_founder_post",
            "text": {"type": "plain_text", "text": "View founder post  →"},
            "url": "https://x.com/Adalat_AI/status/2090071662784086176",
        }
    ],
}


def body_for(payload: dict) -> bytes:
    return urlencode({"payload": json.dumps(payload)}).encode()


def sign(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    digest = hmac.new(
        secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"v0={digest}"


def _with(monkeypatch, **overrides):
    """Settings is a frozen dataclass, so a test swaps the module's reference."""
    patched = replace(app_settings, **overrides)
    monkeypatch.setattr("app.slack_interactions.settings", patched)
    monkeypatch.setattr("app.main.settings", patched)
    return patched


@pytest.fixture
def configured(monkeypatch):
    _with(monkeypatch, slack_signing_secret=SECRET)
    return SECRET


# --- signature verification -------------------------------------------------


def test_valid_signature_is_accepted(configured):
    body, now = body_for(BUTTON_CLICK), int(time.time())
    verify_signature(body, str(now), sign(body, now))


def test_signature_from_a_different_secret_is_rejected(configured):
    body, now = body_for(BUTTON_CLICK), int(time.time())
    with pytest.raises(InteractionError) as caught:
        verify_signature(body, str(now), sign(body, now, secret="not-the-secret"))
    assert caught.value.reason == "bad_signature"
    assert caught.value.status == 401


def test_signature_over_a_different_body_is_rejected(configured):
    """The signature covers the body, so a swapped body must not verify."""
    now = int(time.time())
    signature = sign(body_for(BUTTON_CLICK), now)
    tampered = body_for({**BUTTON_CLICK, "user": {"id": "U999"}})
    with pytest.raises(InteractionError) as caught:
        verify_signature(tampered, str(now), signature)
    assert caught.value.reason == "bad_signature"


@pytest.mark.parametrize(
    "timestamp, signature",
    [(None, "v0=abc"), ("123", None), (None, None)],
)
def test_missing_headers_are_rejected(configured, timestamp, signature):
    with pytest.raises(InteractionError) as caught:
        verify_signature(b"payload=%7B%7D", timestamp, signature)
    assert caught.value.reason == "missing_signature_headers"


def test_a_non_numeric_timestamp_is_rejected(configured):
    with pytest.raises(InteractionError) as caught:
        verify_signature(b"payload=%7B%7D", "not-a-number", "v0=abc")
    assert caught.value.reason == "malformed_timestamp"


def test_a_stale_request_is_rejected_even_with_a_good_signature(configured):
    """This is the replay guard: the capture is authentic but too old to use."""
    body = body_for(BUTTON_CLICK)
    old = int(time.time()) - 3600
    with pytest.raises(InteractionError) as caught:
        verify_signature(body, str(old), sign(body, old))
    assert caught.value.reason == "stale_timestamp"


def test_a_future_dated_request_is_rejected(configured):
    """Checking only the past leaves the window trivially wide."""
    body = body_for(BUTTON_CLICK)
    ahead = int(time.time()) + 3600
    with pytest.raises(InteractionError) as caught:
        verify_signature(body, str(ahead), sign(body, ahead))
    assert caught.value.reason == "stale_timestamp"


def test_without_a_configured_secret_nothing_is_accepted(monkeypatch):
    """Never fall open. An unconfigured deployment answers 503, not 200."""
    _with(monkeypatch, slack_signing_secret="")
    body, now = body_for(BUTTON_CLICK), int(time.time())
    with pytest.raises(InteractionError) as caught:
        verify_signature(body, str(now), sign(body, now))
    assert caught.value.status == 503
    assert caught.value.reason == "signing_secret_not_configured"


# --- payload reading --------------------------------------------------------


def test_a_button_click_is_parsed_and_described():
    payload = parse_payload(body_for(BUTTON_CLICK))
    assert payload["type"] == "block_actions"
    assert describe(payload) == "block_actions:View founder post  →"


@pytest.mark.parametrize(
    "body, reason",
    [
        (b"", "missing_payload"),  # nothing sent at all: absent, not malformed
        (b"notaform", "malformed_body"),
        (b"other=1", "missing_payload"),
        (b"payload=notjson", "malformed_payload"),
        (b"payload=%5B1%2C2%5D", "malformed_payload"),  # JSON, but not an object
        ("payload=%FF".encode("latin-1"), "malformed_payload"),
    ],
)
def test_malformed_bodies_are_refused(body, reason):
    with pytest.raises(InteractionError) as caught:
        parse_payload(body)
    assert caught.value.reason == reason
    assert caught.value.status == 400


def test_describe_reveals_nothing_about_the_user_or_channel():
    """The log line is the easiest place to spill something. It carries the
    interaction's shape and the button's label, and nothing else."""
    summary = describe(BUTTON_CLICK)
    for secret in ("U123", "reviewer", "C123"):
        assert secret not in summary


# --- the endpoint ------------------------------------------------------------


@pytest.fixture
def client():
    # The app's lifespan starts the scheduler; these tests only need routing.
    with TestClient(app) as started:
        yield started


def post(client, body: bytes, timestamp: int, signature: str):
    return client.post(
        "/slack/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(timestamp),
            "X-Slack-Signature": signature,
        },
    )


def test_endpoint_acknowledges_a_real_button_click(configured, client):
    body, now = body_for(BUTTON_CLICK), int(time.time())
    response = post(client, body, now, sign(body, now))
    assert response.status_code == 200


def test_endpoint_refuses_a_forged_signature(configured, client):
    body, now = body_for(BUTTON_CLICK), int(time.time())
    response = post(client, body, now, sign(body, now, secret="wrong"))
    assert response.status_code == 401
    assert response.json() == {"error": "bad_signature"}


def test_endpoint_refuses_an_unsigned_request(configured, client):
    response = client.post("/slack/interactions", content=body_for(BUTTON_CLICK))
    assert response.status_code == 401


def test_endpoint_refuses_a_replayed_request(configured, client):
    body = body_for(BUTTON_CLICK)
    old = int(time.time()) - 3600
    response = post(client, body, old, sign(body, old))
    assert response.status_code == 401
    assert response.json() == {"error": "stale_timestamp"}


def test_endpoint_refuses_a_malformed_but_correctly_signed_body(configured, client):
    """Authentic sender, unusable content: 400 rather than 401 or a crash."""
    body, now = b"payload=notjson", int(time.time())
    response = post(client, body, now, sign(body, now))
    assert response.status_code == 400


def test_no_response_ever_contains_the_signing_secret(configured, client):
    body, now = body_for(BUTTON_CLICK), int(time.time())
    for response in (
        post(client, body, now, sign(body, now)),
        post(client, body, now, sign(body, now, secret="wrong")),
        post(client, b"payload=notjson", now, sign(b"payload=notjson", now)),
        post(client, body, now - 3600, sign(body, now - 3600)),
    ):
        assert SECRET not in response.text
