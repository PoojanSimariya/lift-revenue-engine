"""Unit tests for FastAPI webhook HTTP router."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from lift.api.app import create_app
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.webhooks.router import get_db_session_factory

SECRET = "test_router_secret_123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)

    app = create_app()
    app.dependency_overrides[get_db_session_factory] = lambda: factory

    with TestClient(app) as test_client:
        yield test_client

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_router_missing_event_id_header_returns_400(client):
    """Assert missing x-razorpay-event-id header returns HTTP 400 Bad Request."""
    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={"x-razorpay-signature": "some_sig"},
        content=b"{}",
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "missing_x_razorpay_event_id"


def test_router_missing_signature_header_returns_401(client):
    """Assert missing x-razorpay-signature header returns HTTP 401 Unauthorized."""
    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={"x-razorpay-event-id": "evt_123"},
        content=b"{}",
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_signature_header"


def test_router_invalid_signature_returns_401(client):
    """Assert invalid HMAC signature returns HTTP 401 Unauthorized."""
    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_123",
            "x-razorpay-signature": "invalid_signature_hex",
        },
        content=b'{"event": "payment.failed"}',
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_webhook_signature"


def test_router_valid_payload_returns_200_accepted(client):
    """Assert valid webhook returns HTTP 200 with accepted status."""
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_router_01",
                    "order_id": "order_router_01",
                    "amount": 25000,
                }
            }
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_router_01",
            "x-razorpay-signature": sig,
        },
        content=raw,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["event_id"] == "evt_router_01"


def test_router_duplicate_event_returns_200_duplicate(client):
    """Assert duplicate webhook delivery returns HTTP 200 with duplicate_acknowledged."""
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_router_02",
                    "order_id": "order_router_02",
                    "amount": 25000,
                }
            }
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    headers = {
        "x-razorpay-event-id": "evt_router_dup",
        "x-razorpay-signature": sig,
    }

    # First delivery
    resp1 = client.post("/api/v1/webhooks/razorpay", headers=headers, content=raw)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "accepted"

    # Duplicate delivery
    resp2 = client.post("/api/v1/webhooks/razorpay", headers=headers, content=raw)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate_acknowledged"


def test_router_malformed_json_returns_400(client):
    """Assert unparseable malformed body returns HTTP 400 Bad Request."""
    raw = b"{ malformed json string "
    sig = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_malformed",
            "x-razorpay-signature": sig,
        },
        content=raw,
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "malformed_json"


def test_health_check_endpoint(client):
    """Assert health check returns ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_router_internal_error_does_not_leak_exception_details(client, monkeypatch):
    """Assert HTTP 500 returns generic external error without leaking internal exception strings."""
    from lift.webhooks.service import WebhookIngestionService

    secret_leak_msg = "Database deadlocks at postgresql://user:secretpass@db/prod"

    def mock_process_webhook(*args, **kwargs):
        raise RuntimeError(secret_leak_msg)

    monkeypatch.setattr(WebhookIngestionService, "process_webhook", mock_process_webhook)

    raw = b'{"event": "payment.failed"}'
    sig = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    resp = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_crash_500",
            "x-razorpay-signature": sig,
        },
        content=raw,
    )
    assert resp.status_code == 500
    data = resp.json()
    assert data == {"error": "internal_error"}
    assert "message" not in data
    assert secret_leak_msg not in resp.text
