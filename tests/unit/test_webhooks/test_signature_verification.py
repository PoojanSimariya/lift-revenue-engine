"""Unit tests for webhook HMAC-SHA256 signature verification."""

import hashlib
import hmac

from lift.webhooks.verifier import verify_webhook_signature


def test_signature_verification_valid():
    """Verify that a valid HMAC-SHA256 signature passes verification."""
    secret = "rzp_webhook_secret_xyz"
    raw_body = b'{"entity":"event","event":"payment.captured"}'
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(raw_body, valid_sig, secret) is True


def test_signature_verification_tampered_body():
    """Verify that a 1-byte alteration in the raw body fails verification."""
    secret = "rzp_webhook_secret_xyz"
    raw_body = b'{"entity":"event","event":"payment.captured"}'
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    tampered_body = b'{"entity":"event","event":"payment.captured" }'  # extra space
    assert verify_webhook_signature(tampered_body, valid_sig, secret) is False


def test_signature_verification_wrong_secret():
    """Verify that using an incorrect secret fails verification."""
    secret = "rzp_webhook_secret_xyz"
    wrong_secret = "wrong_secret_123"
    raw_body = b'{"entity":"event","event":"payment.captured"}'
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(raw_body, valid_sig, wrong_secret) is False


def test_missing_signature_header():
    """Verify that None or empty signature string returns False."""
    secret = "rzp_webhook_secret_xyz"
    raw_body = b'{"entity":"event"}'

    assert verify_webhook_signature(raw_body, None, secret) is False
    assert verify_webhook_signature(raw_body, "", secret) is False


def test_empty_secret_or_body():
    """Verify that empty secret or empty body returns False."""
    assert verify_webhook_signature(b"", "sig123", "secret") is False
    assert verify_webhook_signature(b"body", "sig123", "") is False
    assert verify_webhook_signature(b"body", "sig123", None) is False
