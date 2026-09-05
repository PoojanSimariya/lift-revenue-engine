"""Deterministic reference_id generation and validation for Razorpay Payment Links."""

import hashlib
import re
from uuid import UUID

_REF_REGEX = re.compile(r"^ref_[0-9a-f]{32}$")


def generate_reference_id(opportunity_id: str | UUID, attempt_index: int) -> str:
    """Generate a collision-resistant, deterministic reference_id for a Payment Link.

    Constructed as:
        payload = str(opportunity_id) + ":" + str(attempt_index)
        digest = SHA256(payload.encode("utf-8")).hexdigest()[:32]
        return "ref_" + digest

    Properties:
        - Exactly 36 characters (<= 40 characters required by Razorpay).
        - 128 bits of SHA-256 collision resistance.
        - Strictly non-reversible: opportunity_id CANNOT be decoded from the hash.
        - Deterministic across processes, servers, and restarts.
    """
    payload = f"{str(opportunity_id)}:{attempt_index}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"ref_{digest}"


def is_valid_reference_id(reference_id: str) -> bool:
    """Validate whether a reference_id complies with LIFT deterministic format."""
    if not isinstance(reference_id, str):
        return False
    return bool(_REF_REGEX.match(reference_id))
