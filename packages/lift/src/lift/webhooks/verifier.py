"""Cryptographic webhook signature verifier."""

import hashlib
import hmac


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str | None) -> bool:
    """Verify constant-time HMAC-SHA256 signature against raw webhook bytes.

    Guarantees timing-attack resistance using hmac.compare_digest.
    """
    if not signature or not secret or not raw_body:
        return False

    computed = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, signature)
