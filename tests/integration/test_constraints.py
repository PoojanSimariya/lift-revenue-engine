"""Integration tests verifying database constraints, duplicate conflicts,
and cascading delete behaviors.
"""

from __future__ import annotations

import pytest
from lift.core.errors import IdempotencyConflictError
from lift.core.types import DecisionType, InterventionType
from lift.domain.models import (
    AuditEvent,
    Customer,
    ExecutionRecord,
    InterventionCandidate,
    Merchant,
    PaymentAttempt,
    PaymentEvidence,
    RecoveryDecision,
    RecoveryOpportunity,
)
from lift.storage.orm_models import (
    AuditEventORM,
    CustomerORM,
    InterventionCandidateORM,
    MerchantORM,
    PaymentAttemptORM,
    PaymentEvidenceORM,
    RecoveryDecisionORM,
    RecoveryOpportunityORM,
)
from lift.storage.repositories import (
    AuditEventRepository,
    CandidateRepository,
    DecisionRepository,
    ExecutionRecordRepository,
    OpportunityRepository,
    PaymentEvidenceRepository,
    WebhookEventRepository,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_customer_merchant_and_external_id_unique(
    session: Session, persisted_merchant: Merchant
) -> None:
    """Duplicate external_customer_id within same merchant violates unique constraint."""
    c1 = CustomerORM(
        merchant_id=persisted_merchant.id,
        external_customer_id="duplicate_ext_id_001",
    )
    session.add(c1)
    session.flush()

    c2 = CustomerORM(
        merchant_id=persisted_merchant.id,
        external_customer_id="duplicate_ext_id_001",
    )
    session.add(c2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_webhook_deduplication_raises_conflict(session: Session) -> None:
    """Duplicate webhook event ID raises IdempotencyConflictError."""
    repo = WebhookEventRepository(session)
    event_id = "evt_razorpay_duplicate_123"

    recorded = repo.record_event(
        event_id=event_id,
        event_type="payment.failed",
        payload={"event": "payment.failed", "id": event_id},
    )
    assert recorded.event_id == event_id
    assert recorded.status == "PENDING"

    # Second insert with same event_id must raise IdempotencyConflictError
    with pytest.raises(IdempotencyConflictError, match="Duplicate webhook event ID"):
        repo.record_event(
            event_id=event_id,
            event_type="payment.failed",
            payload={"event": "payment.failed"},
        )

    # Mark processed test
    processed = repo.mark_processed(event_id)
    assert processed.status == "PROCESSED"
    assert processed.processed_at is not None


def test_execution_voucher_idempotency_conflict(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Duplicate idempotency key on execution vouchers raises IdempotencyConflictError."""
    opp_repo = OpportunityRepository(session)
    opp, _ = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    dec_repo = DecisionRepository(session)
    decision = dec_repo.create(
        RecoveryDecision(
            opportunity_id=opp.id,
            selected_candidate_id=None,
            decision_type=DecisionType.AUTHORIZED,
            policy_evaluation_details={},
            explanation="Approved",
        )
    )

    exec_repo = ExecutionRecordRepository(session)
    voucher1 = ExecutionRecord(
        decision_id=decision.id,
        attempt_index=1,
        idempotency_key="idemp_voucher_conflict_key",
        reference_id="ref_voucher_1",
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
    )
    exec_repo.create_voucher(voucher1)

    voucher2 = ExecutionRecord(
        decision_id=decision.id,
        attempt_index=2,
        idempotency_key="idemp_voucher_conflict_key",
        reference_id="ref_voucher_2",
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
    )
    with pytest.raises(IdempotencyConflictError):
        exec_repo.create_voucher(voucher2)


def test_merchant_cascade_delete(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Deleting a merchant cascades to customers, rules, opportunities, and audit events."""
    opp_repo = OpportunityRepository(session)
    opp, _ = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    audit_repo = AuditEventRepository(session)
    audit_repo.record_event(
        AuditEvent(
            merchant_id=persisted_merchant.id,
            trace_id="trc_cascade",
            aggregate_type="Merchant",
            aggregate_id=persisted_merchant.id,
            event_name="CREATED",
        )
    )

    # Delete merchant
    session.execute(delete(MerchantORM).where(MerchantORM.id == persisted_merchant.id))
    session.flush()

    # Verify cascaded deletions
    assert (
        session.scalar(select(CustomerORM).where(CustomerORM.id == persisted_customer.id)) is None
    )
    assert (
        session.scalar(select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opp.id))
        is None
    )
    assert (
        session.scalar(
            select(AuditEventORM).where(AuditEventORM.merchant_id == persisted_merchant.id)
        )
        is None
    )


def test_opportunity_delete_sets_attempt_fk_to_null(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Deleting opportunity cascades to children and sets attempt opportunity FK to NULL."""
    opp_repo = OpportunityRepository(session)
    opp, attempt = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    cand_repo = CandidateRepository(session)
    candidate = cand_repo.create(
        InterventionCandidate(
            opportunity_id=opp.id,
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            parameters={},
            p_recovery=0.5,
            p_organic=0.1,
            direct_cost_subunits=100,
            friction_cost_subunits=50,
            risk_penalty_subunits=0,
            expected_net_value_subunits=1000,
            confidence_score=0.8,
        )
    )

    dec_repo = DecisionRepository(session)
    decision = dec_repo.create(
        RecoveryDecision(
            opportunity_id=opp.id,
            selected_candidate_id=candidate.id,
            decision_type=DecisionType.AUTHORIZED,
            policy_evaluation_details={},
            explanation="Approved",
        )
    )

    evidence_repo = PaymentEvidenceRepository(session)
    evidence_repo.create(
        PaymentEvidence(
            opportunity_id=opp.id,
            razorpay_payment_id="pay_cascade_evidence_001",
            event_type="payment.captured",
            signature_hash="sha256_hash",
            captured_amount_subunits=1000,
        )
    )

    # Delete opportunity
    # When opp is deleted, payment_attempts.recovery_opportunity_id is SET NULL.
    session.execute(delete(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opp.id))
    session.flush()

    # 1. Payment attempt still exists!
    attempt_orm = session.scalar(
        select(PaymentAttemptORM).where(PaymentAttemptORM.id == attempt.id)
    )
    assert attempt_orm is not None
    # 2. payment_attempt.recovery_opportunity_id was SET NULL!
    assert attempt_orm.recovery_opportunity_id is None

    # 3. Children of opportunity were CASCADE deleted
    assert (
        session.scalar(
            select(InterventionCandidateORM).where(InterventionCandidateORM.id == candidate.id)
        )
        is None
    )
    assert (
        session.scalar(select(RecoveryDecisionORM).where(RecoveryDecisionORM.id == decision.id))
        is None
    )
    assert (
        session.scalar(
            select(PaymentEvidenceORM).where(
                PaymentEvidenceORM.razorpay_payment_id == "pay_cascade_evidence_001"
            )
        )
        is None
    )
