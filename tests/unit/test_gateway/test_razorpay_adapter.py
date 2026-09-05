"""Unit tests for RazorpayTestModeAdapter."""

import hashlib
import hmac
import inspect

import httpx
import pytest
from lift.core.errors import (
    GatewayAuthenticationError,
    GatewayError,
    GatewayResourceNotFoundError,
    GatewayTimeoutError,
)
from lift.gateway.interface import PaymentGatewayAdapter
from lift.gateway.razorpay_adapter import RazorpayTestModeAdapter
from lift.gateway.types import GatewayCustomerInfo


def test_adapter_no_fictitious_methods():
    """Verify that PaymentGatewayAdapter does not expose fictitious methods."""
    members = [m[0] for m in inspect.getmembers(PaymentGatewayAdapter)]
    assert "trigger_smart_retry" not in members, (
        "trigger_smart_retry is fictitious and must not exist"
    )
    assert "smart_retry" not in members
    assert "auto_debit" not in members


def test_razorpay_adapter_signature_verification():
    """Verify HMAC-SHA256 signature verification logic."""
    adapter = RazorpayTestModeAdapter(key_id="rzp_test_key", key_secret="rzp_test_sec")
    raw_body = b'{"event": "payment.captured", "id": "pay_123"}'
    secret = "secret_webhook_123"
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    assert adapter.verify_webhook_signature(raw_body, valid_sig, secret) is True
    assert adapter.verify_webhook_signature(raw_body, "invalid_sig", secret) is False
    assert adapter.verify_webhook_signature(b"tampered", valid_sig, secret) is False
    assert adapter.verify_webhook_signature(raw_body, valid_sig, "") is False


def test_razorpay_adapter_create_payment_link(monkeypatch):
    """Verify create_payment_link payload structure and response parsing."""
    captured_request = {}

    def mock_request(method, url, **kwargs):
        captured_request["method"] = method
        captured_request["url"] = url
        captured_request["json"] = kwargs.get("json")
        return httpx.Response(
            status_code=200,
            json={
                "id": "plink_test_001",
                "short_url": "https://rzp.io/i/test",
                "reference_id": "ref_abc123",
                "status": "created",
                "amount": 50000,
                "currency": "INR",
                "notes": {"opportunity_id": "opp_1", "attempt_index": "0"},
            },
        )

    adapter = RazorpayTestModeAdapter(key_id="key", key_secret="secret")
    adapter._client.request = mock_request

    cust = GatewayCustomerInfo(name="Test User", email="test@example.com", contact="+919876543210")
    result = adapter.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id="ref_abc123",
        description="Recovery Link",
        customer=cust,
        notes={"opportunity_id": "opp_1", "attempt_index": "0"},
    )

    assert result.id == "plink_test_001"
    assert result.reference_id == "ref_abc123"
    assert result.amount == 50000
    assert captured_request["json"]["accept_partial"] is False, "M3 requires accept_partial=False"
    assert captured_request["json"]["reminder_enable"] is False
    assert captured_request["json"]["notes"]["opportunity_id"] == "opp_1"


def test_razorpay_adapter_authentication_error(monkeypatch):
    """Verify HTTP 401 raises GatewayAuthenticationError."""

    def mock_request(method, url, **kwargs):
        return httpx.Response(status_code=401, json={"error": {"code": "BAD_REQUEST_ERROR"}})

    adapter = RazorpayTestModeAdapter(key_id="invalid", key_secret="invalid")
    adapter._client.request = mock_request

    with pytest.raises(GatewayAuthenticationError) as exc_info:
        adapter.fetch_payment("pay_123")
    assert "401" in str(exc_info.value)


def test_razorpay_adapter_resource_not_found_error(monkeypatch):
    """Verify HTTP 404 raises GatewayResourceNotFoundError."""

    def mock_request(method, url, **kwargs):
        return httpx.Response(status_code=404, json={"error": {"code": "NOT_FOUND"}})

    adapter = RazorpayTestModeAdapter(key_id="key", key_secret="secret")
    adapter._client.request = mock_request

    with pytest.raises(GatewayResourceNotFoundError):
        adapter.fetch_payment("pay_missing")


def test_razorpay_adapter_timeout_error(monkeypatch):
    """Verify timeout raises GatewayTimeoutError."""

    def mock_request(method, url, **kwargs):
        raise httpx.TimeoutException("Connection timed out")

    adapter = RazorpayTestModeAdapter(key_id="key", key_secret="secret")
    adapter._client.request = mock_request

    with pytest.raises(GatewayTimeoutError):
        adapter.fetch_payment("pay_123")


def test_razorpay_adapter_general_gateway_error(monkeypatch):
    """Verify generic 400 error raises GatewayError."""

    def mock_request(method, url, **kwargs):
        return httpx.Response(
            status_code=400,
            json={"error": {"code": "BAD_REQUEST_ERROR", "description": "Amount exceeds maximum"}},
        )

    adapter = RazorpayTestModeAdapter(key_id="key", key_secret="secret")
    adapter._client.request = mock_request

    with pytest.raises(GatewayError) as exc_info:
        adapter.fetch_payment("pay_123")
    assert exc_info.value.gateway_code == "BAD_REQUEST_ERROR"
    assert "Amount exceeds maximum" in str(exc_info.value)
