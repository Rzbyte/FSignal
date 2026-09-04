"""Who can read what.

Three endpoints carry the leads themselves -- the dashboard, the ledger and the
per-signal timelines -- and on a public deployment that is the pipeline a GTM
user is relying on being alone in having. DASHBOARD_TOKEN gates them.

The two things that must survive the gate are /health, which carries counts
rather than companies, and the Pond endpoints, which are how the agent is
health-checked. Gating either would trade a real capability for no privacy.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.main import app

TOKEN = "test-dashboard-token"

#: Everything that must refuse an unauthenticated caller once a token is set.
LEAD_ENDPOINTS = ("/", "/ledger", "/signals/1/timeline")

#: Everything that must keep answering regardless.
ALWAYS_OPEN = ("/health", "/manifest")


@pytest.fixture
def client():
    with TestClient(app) as started:
        yield started


def _token(monkeypatch, value):
    monkeypatch.setattr("app.main.settings", replace(app_settings, dashboard_token=value))


# --- default posture: open, and honest about being open ----------------------


@pytest.mark.parametrize("path", LEAD_ENDPOINTS)
def test_without_a_token_the_lead_endpoints_stay_open(monkeypatch, client, path):
    """The evidence in this repo is only checkable while these can be opened."""
    _token(monkeypatch, "")
    assert client.get(path).status_code in (200, 404)


def test_health_reports_the_posture(monkeypatch, client):
    _token(monkeypatch, "")
    assert client.get("/health").json()["lead_endpoints_protected"] is False

    _token(monkeypatch, TOKEN)
    assert client.get("/health").json()["lead_endpoints_protected"] is True


# --- gated posture ------------------------------------------------------------


@pytest.mark.parametrize("path", LEAD_ENDPOINTS)
def test_a_token_closes_the_lead_endpoints(monkeypatch, client, path):
    _token(monkeypatch, TOKEN)
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("path", LEAD_ENDPOINTS)
def test_the_right_bearer_token_opens_them(monkeypatch, client, path):
    _token(monkeypatch, TOKEN)
    response = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code in (200, 404)


def test_a_query_token_also_works(monkeypatch, client):
    """A browser cannot set a header, and these are pages a person opens."""
    _token(monkeypatch, TOKEN)
    assert client.get(f"/ledger?token={TOKEN}").status_code == 200


@pytest.mark.parametrize("supplied", ["", "wrong", TOKEN[:-1], TOKEN + "x"])
def test_a_wrong_token_is_refused(monkeypatch, client, supplied):
    _token(monkeypatch, TOKEN)
    response = client.get("/ledger", headers={"Authorization": f"Bearer {supplied}"})
    assert response.status_code == 401


def test_a_refusal_never_echoes_the_expected_token(monkeypatch, client):
    _token(monkeypatch, TOKEN)
    response = client.get("/ledger", headers={"Authorization": "Bearer wrong"})
    assert TOKEN not in response.text


# --- what the gate must never touch -------------------------------------------


@pytest.mark.parametrize("path", ALWAYS_OPEN)
def test_health_and_manifest_are_never_gated(monkeypatch, client, path):
    """Pond health-checks the agent through these. Gating them would break the
    integration to protect data neither of them carries."""
    _token(monkeypatch, TOKEN)
    assert client.get(path).status_code == 200


def test_health_carries_no_company_names(monkeypatch, client):
    """The reason /health can stay public: it reports counts, not companies."""
    _token(monkeypatch, "")
    body = client.get("/health").json()
    assert "stats" in body and "sources" in body
    for key in ("ghosts", "candidates", "companies", "signals_detail"):
        assert not isinstance(body.get(key), list)


def test_pond_runs_still_answers_on_its_own_key_not_the_dashboard_one(
    monkeypatch, client
):
    """The two credentials are separate. A dashboard token must not open /runs."""
    _token(monkeypatch, TOKEN)
    response = client.post(
        "/runs",
        json={},
        headers={"Authorization": f"Bearer {TOKEN}", "X-Agent-Protocol-Version": "1.0"},
    )
    assert response.status_code in (401, 503)
