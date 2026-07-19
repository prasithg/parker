"""Auth seam for the caregiver decision surface.

What these pin down: auth is OFF by default (empty password — localhost
demos stay zero-config); once a password is set, the review feed/page,
outbox, and action mutations all require correct HTTP Basic credentials
(401 + WWW-Authenticate challenge otherwise); and the assistant-loop
surface (/tick, /resurface) deliberately stays open either way.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

CREDS = ("family", "open-sesame")

PROTECTED = [
    ("GET", "/parker/review"),
    ("GET", "/parker/review/ui"),
    ("GET", "/parker/outbox"),
    ("POST", "/parker/actions/999/confirm"),
    ("POST", "/parker/actions/999/execute"),
    ("POST", "/parker/actions/999/cancel"),
    ("POST", "/parker/outbox/999/approve"),
    ("POST", "/parker/outbox/999/cancel"),
    ("POST", "/parker/research-handoffs/999/complete"),
    ("POST", "/parker/research-handoffs/999/cancel"),
]


@pytest.fixture
def password_set(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", CREDS[0])
    monkeypatch.setattr(settings, "dashboard_password", CREDS[1])


def _request(method, path, **kwargs):
    if method == "GET":
        return client.get(path, **kwargs)
    return client.request(method, path, json={}, **kwargs)


def test_auth_disabled_by_default_everything_open():
    assert settings.dashboard_password == ""
    assert client.get("/parker/review").status_code == 200
    assert client.get("/parker/review/ui").status_code == 200
    assert client.get("/parker/outbox").status_code == 200


@pytest.mark.parametrize("method,path", PROTECTED)
def test_password_set_requires_credentials(password_set, method, path):
    response = _request(method, path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Basic"


@pytest.mark.parametrize(
    "bad_creds",
    [("family", "wrong"), ("intruder", "open-sesame"), ("", "")],
)
def test_wrong_credentials_rejected(password_set, bad_creds):
    response = client.get("/parker/review", auth=bad_creds)
    assert response.status_code == 401


def test_correct_credentials_accepted(password_set):
    assert client.get("/parker/review", auth=CREDS).status_code == 200
    assert client.get("/parker/review/ui", auth=CREDS).status_code == 200
    assert client.get("/parker/outbox", auth=CREDS).status_code == 200
    # Mutations authenticate, then fail on the missing row — auth layer
    # and resource layer stay distinguishable.
    assert client.post("/parker/actions/999/confirm", json={}, auth=CREDS).status_code == 404
    assert client.post("/parker/outbox/999/approve", json={}, auth=CREDS).status_code == 404


def test_assistant_loop_surface_stays_open(password_set):
    assert client.post("/parker/tick", json={}).status_code == 200
    assert client.get("/parker/resurface").status_code == 200
