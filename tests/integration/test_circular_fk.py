"""Integration tests for the 3-step circular FK insertion protocol and mutual relationships."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

import pytest
from lift.core.types import AttemptStatus, OpportunityState, PaymentMethod
from lift.domain.models import Customer, Merchant, PaymentAttempt, RecoveryOpportunity
from lift.storage.orm_models import PaymentAttemptORM, RecoveryOpportunityORM
from lift.storage.repositories.opportunity import OpportunityRepository
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_three_step_circular_fk_insertion_success(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Verify that the 3-step circular FK insertion succeeds atomically within a transaction."""
    repo = OpportunityRepository(session)

    # Execute within caller transaction
    with session.begin_nested():
        opp, attempt = repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    # Verify both entities return mapped domain representations
    assert opp.id == sample_opportunity.id
    assert attempt.id == sample_attempt.id
    assert opp.initial_attempt_id == attempt.id
    assert opp.latest_attempt_id == attempt.id

    # Verify in the database via ORM queries
    opp_orm = session.scalar(
        select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opp.id)
    )
    attempt_orm = session.scalar(
        select(PaymentAttemptORM).where(PaymentAttemptORM.id == attempt.id)
    )

    assert opp_orm is not None
    assert attempt_orm is not None
    assert opp_orm.initial_attempt_id == attempt_orm.id
    assert opp_orm.latest_attempt_id == attempt_orm.id
    assert attempt_orm.recovery_opportunity_id == opp_orm.id


def test_three_step_circular_fk_step2_failure_rollback(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """If Step 2 or Step 3 fails, the outer transaction must roll back completely."""
    repo = OpportunityRepository(session)

    # Pre-insert an opportunity with the same order_id
    # to trigger a unique constraint violation at Step 2
    with session.begin_nested():
        repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    # Create a second attempt and a conflicting second opportunity (same order_id)
    second_attempt = copy.deepcopy(sample_attempt)
    second_attempt.razorpay_payment_id = "pay_conflicting_unique_002"
    second_attempt.id = uuid.uuid4()

    conflicting_opp = copy.deepcopy(sample_opportunity)
    conflicting_opp.id = uuid.uuid4()
    # Keep same order_id: conflicting_opp.order_id == sample_opportunity.order_id

    # Attempting insertion within a transaction must raise and roll back
    with pytest.raises(Exception):  # noqa: B017
        with session.begin_nested():
            repo.create_with_initial_attempt(conflicting_opp, second_attempt)

    # Verify second attempt was NOT persisted (no half-inserted orphan row)
    orphan_check = session.scalar(
        select(PaymentAttemptORM).where(PaymentAttemptORM.id == second_attempt.id)
    )
    assert orphan_check is None


def test_transaction_boundary_caller_rollback(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """If caller encounters an error after repository call, entire transaction rolls back."""
    repo = OpportunityRepository(session)

    with pytest.raises(RuntimeError, match="External boundary failure"):
        with session.begin_nested():
            repo.create_with_initial_attempt(sample_opportunity, sample_attempt)
            raise RuntimeError("External boundary failure")

    # Neither opportunity nor attempt should be persisted
    opp_check = session.scalar(
        select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == sample_opportunity.id)
    )
    attempt_check = session.scalar(
        select(PaymentAttemptORM).where(PaymentAttemptORM.id == sample_attempt.id)
    )
    assert opp_check is None
    assert attempt_check is None


def test_opportunity_update(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Verify updating mutable lifecycle fields on an opportunity."""
    repo = OpportunityRepository(session)

    with session.begin_nested():
        opp, _ = repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    # Mutate domain fields
    opp.current_state = OpportunityState.IN_EVALUATION
    opp.version = 2
    opp.last_evaluated_at = datetime.now(timezone.utc)
    opp.failure_attempt_count = 2
    opp.total_interventions_count = 1

    with session.begin_nested():
        updated_opp = repo.update(opp)

    assert updated_opp.current_state == OpportunityState.IN_EVALUATION
    assert updated_opp.version == 2
    assert updated_opp.failure_attempt_count == 2
    assert updated_opp.total_interventions_count == 1

    # Verify persisted in database
    opp_orm = session.scalar(
        select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opp.id)
    )
    assert opp_orm is not None
    assert opp_orm.current_state == "IN_EVALUATION"
    assert opp_orm.version == 2


def test_associate_additional_attempt(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Verify associating a second attempt updates latest_attempt_id.

    initial_attempt_id must remain preserved.
    """
    repo = OpportunityRepository(session)

    with session.begin_nested():
        opp, initial_att = repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    # Create a 2nd payment attempt
    second_attempt = PaymentAttempt(
        customer_id=persisted_customer.id,
        razorpay_payment_id="pay_attempt_002",
        razorpay_order_id=sample_attempt.razorpay_order_id,
        attempt_sequence=2,
        amount_subunits=sample_attempt.amount_subunits,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        status=AttemptStatus.FAILED,
        error_code="GATEWAY_ERROR",
        gateway_created_at=datetime.now(timezone.utc),
    )

    with session.begin_nested():
        updated_opp, created_att2 = repo.associate_additional_attempt(opp.id, second_attempt)

    # Assertions
    assert updated_opp.initial_attempt_id == initial_att.id
    assert updated_opp.latest_attempt_id == created_att2.id
    assert created_att2.recovery_opportunity_id == opp.id

    # Verify in database
    opp_orm = session.scalar(
        select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opp.id)
    )
    assert opp_orm is not None
    assert opp_orm.initial_attempt_id == initial_att.id
    assert opp_orm.latest_attempt_id == created_att2.id
