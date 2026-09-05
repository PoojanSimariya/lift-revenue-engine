"""Unit tests for ExecutionVoucherService, reference_id, and salted SHA-256 idempotency keys."""

from uuid import uuid4

import pytest
from lift.core.errors import DataValidationError
from lift.core.types import ExecutionStatus, InterventionType
from lift.services.voucher import (
    ExecutionVoucherService,
    generate_idempotency_key,
    generate_reference_id,
)


def test_reference_id_generation() -> None:
    opp_id = uuid4()
    ref_1 = generate_reference_id(opp_id, attempt_index=1)
    ref_2 = generate_reference_id(opp_id, attempt_index=2)

    # 1. Deterministic formatting
    clean_id = str(opp_id).replace("-", "")
    assert ref_1 == f"ref_{clean_id[:16]}_1"
    assert ref_2 == f"ref_{clean_id[:16]}_2"
    assert ref_1 != ref_2

    # 2. Maximum length strictly <= 40 characters
    assert len(ref_1) <= 40
    assert len(ref_2) <= 40

    # 3. Same inputs produce exact same reference_id
    assert generate_reference_id(opp_id, attempt_index=1) == ref_1


def test_reference_id_invalid_attempt_index() -> None:
    opp_id = uuid4()
    with pytest.raises(DataValidationError):
        generate_reference_id(opp_id, attempt_index=0)

    with pytest.raises(DataValidationError):
        generate_reference_id(opp_id, attempt_index=-1)


def test_idempotency_key_canonical_salted_hashing() -> None:
    opp_id = uuid4()
    salt = "secret_merchant_salt_12345"
    itype = InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP

    key_1 = generate_idempotency_key(opp_id, itype, attempt_index=1, merchant_salt=salt)
    key_2 = generate_idempotency_key(opp_id, itype, attempt_index=1, merchant_salt=salt)
    key_diff_index = generate_idempotency_key(opp_id, itype, attempt_index=2, merchant_salt=salt)
    key_diff_salt = generate_idempotency_key(
        opp_id, itype, attempt_index=1, merchant_salt="other_salt"
    )

    # 1. Deterministic hashing: same inputs produce identical key
    assert key_1 == key_2
    assert len(key_1) == 64  # SHA-256 hex digest length

    # 2. Distinct inputs produce distinct keys
    assert key_1 != key_diff_index
    assert key_1 != key_diff_salt


def test_idempotency_key_validation() -> None:
    opp_id = uuid4()
    with pytest.raises(DataValidationError):
        generate_idempotency_key(opp_id, InterventionType.NO_ACTION, 1, merchant_salt="")

    with pytest.raises(DataValidationError):
        generate_idempotency_key(opp_id, InterventionType.NO_ACTION, 0, merchant_salt="salt")


def test_execution_voucher_preparation() -> None:
    decision_id = uuid4()
    opp_id = uuid4()
    salt = "salt_xyz_123"

    voucher = ExecutionVoucherService.prepare_voucher(
        decision_id=decision_id,
        opportunity_id=opp_id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        attempt_index=1,
        merchant_salt=salt,
    )

    assert voucher.decision_id == decision_id
    assert voucher.attempt_index == 1
    assert voucher.execution_status == ExecutionStatus.CLAIMED
    assert voucher.reference_id.startswith("ref_")
    assert len(voucher.reference_id) <= 40
    assert len(voucher.idempotency_key) == 64
    assert voucher.executed_at is None


def test_voucher_rejects_unknown_intervention_type() -> None:
    with pytest.raises(DataValidationError):
        ExecutionVoucherService.prepare_voucher(
            decision_id=uuid4(),
            opportunity_id=uuid4(),
            intervention_type="INVALID_TYPE",
            attempt_index=1,
            merchant_salt="salt_123",
        )
