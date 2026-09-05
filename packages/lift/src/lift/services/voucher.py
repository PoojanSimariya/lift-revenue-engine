"""Deterministic execution voucher and idempotency key generator."""

from __future__ import annotations

import hashlib
from uuid import UUID

from lift.core.errors import DataValidationError
from lift.core.types import ExecutionStatus, InterventionType
from lift.domain.models import ExecutionRecord
from lift.economics.fatigue import resolve_intervention_type


def generate_reference_id(opportunity_id: UUID | str, attempt_index: int) -> str:
    """Generate a deterministic reference_id guaranteed to be <= 40 characters.

    Formula:
        reference_id = "ref_" + str(opportunity_id).replace("-", "")[:8] + "_" + str(attempt_index)
    """
    if attempt_index < 1:
        raise DataValidationError("attempt_index", attempt_index, "Attempt index must be >= 1.")

    clean_opp_id = str(opportunity_id).replace("-", "")
    short_opp_id = clean_opp_id[:16]
    ref_id = f"ref_{short_opp_id}_{attempt_index}"

    if len(ref_id) > 40:
        raise DataValidationError(
            "reference_id", ref_id, f"Generated reference_id exceeded 40 chars: {len(ref_id)}"
        )

    return ref_id


def generate_idempotency_key(
    opportunity_id: UUID | str,
    intervention_type: InterventionType | str,
    attempt_index: int,
    merchant_salt: str,
) -> str:
    """Generate canonical salted SHA-256 idempotency key.

    Formula:
        idempotency_key = SHA256(UTF8(opp_id:type:attempt_index:merchant_salt))
    """
    if not merchant_salt:
        raise DataValidationError(
            "merchant_salt", merchant_salt, "Merchant idempotency salt cannot be empty."
        )
    if attempt_index < 1:
        raise DataValidationError("attempt_index", attempt_index, "Attempt index must be >= 1.")

    type_str = (
        intervention_type.value
        if isinstance(intervention_type, InterventionType)
        else str(intervention_type)
    )
    canonical_str = f"{opportunity_id}:{type_str}:{attempt_index}:{merchant_salt}"
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


class ExecutionVoucherService:
    """Service to prepare deterministic Phase 1 execution vouchers."""

    @staticmethod
    def prepare_voucher(
        decision_id: UUID,
        opportunity_id: UUID | str,
        intervention_type: InterventionType | str,
        attempt_index: int,
        merchant_salt: str,
    ) -> ExecutionRecord:
        """Create a deterministic Phase 1 ExecutionRecord voucher in CLAIMED state."""
        resolved_type = resolve_intervention_type(intervention_type)
        ref_id = generate_reference_id(opportunity_id, attempt_index)
        idem_key = generate_idempotency_key(
            opportunity_id=opportunity_id,
            intervention_type=resolved_type,
            attempt_index=attempt_index,
            merchant_salt=merchant_salt,
        )

        return ExecutionRecord(
            decision_id=decision_id,
            attempt_index=attempt_index,
            idempotency_key=idem_key,
            reference_id=ref_id,
            intervention_type=resolved_type,
            execution_status=ExecutionStatus.CLAIMED,
        )
