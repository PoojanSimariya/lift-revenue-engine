"""Integration tests for all repository implementations verifying round-trip persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from lift.core.types import (
    ActorType,
    AttemptStatus,
    DecisionType,
    ExecutionStatus,
    InterventionType,
    PaymentMethod,
    RuleType,
)
from lift.domain.models import (
    AuditEvent,
    Customer,
    ExecutionRecord,
    InterventionCandidate,
    Merchant,
    PaymentAttempt,
    PaymentEvidence,
    PolicyRule,
    RecoveryDecision,
    RecoveryOpportunity,
)
from lift.storage.repositories import (
    AuditEventRepository,
    CandidateRepository,
    CustomerRepository,
    DecisionRepository,
    ExecutionRecordRepository,
    MerchantRepository,
    OpportunityRepository,
    PaymentAttemptRepository,
    PaymentEvidenceRepository,
    PolicyRuleRepository,
)
from sqlalchemy.orm import Session


def test_customer_repository_roundtrip(session: Session, persisted_merchant: Merchant) -> None:
    """Test CustomerRepository create, get_by_id, get_by_external_id, and update."""
    repo = CustomerRepository(session)
    customer = Customer(
        merchant_id=persisted_merchant.id,
        external_customer_id="cust_unique_roundtrip_001",
        phone_hash="phone_hash_001",
        email_hash="email_hash_001",
        risk_tier=2,
    )

    created = repo.create(customer)
    assert created.id == customer.id

    by_id = repo.get_by_id(customer.id)
    assert by_id is not None
    assert by_id.external_customer_id == "cust_unique_roundtrip_001"
    assert by_id.risk_tier == 2

    by_ext = repo.get_by_external_id(persisted_merchant.id, "cust_unique_roundtrip_001")
    assert by_ext is not None
    assert by_ext.id == customer.id

    # Update customer metrics
    by_id.rolling_contacts_7d = 3
    by_id.lifetime_recovery_count = 1
    now = datetime.now(timezone.utc)
    by_id.last_contacted_at = now
    updated = repo.update(by_id)

    assert updated.rolling_contacts_7d == 3
    assert updated.lifetime_recovery_count == 1
    assert updated.last_contacted_at == now


def test_merchant_repository_roundtrip(session: Session) -> None:
    """Test MerchantRepository create and get_by_id."""
    repo = MerchantRepository(session)
    merchant = Merchant(
        name="Acme Superstore",
        default_currency="INR",
        timezone="Asia/Kolkata",
        idempotency_salt="merchant_salt_acme_123",
    )
    created = repo.create(merchant)
    assert created.id == merchant.id

    fetched = repo.get_by_id(merchant.id)
    assert fetched is not None
    assert fetched.name == "Acme Superstore"
    assert fetched.idempotency_salt == "merchant_salt_acme_123"


def test_payment_attempt_repository_roundtrip(
    session: Session, persisted_customer: Customer
) -> None:
    """Test PaymentAttemptRepository create, get_by_id, get_by_payment_id, update."""
    repo = PaymentAttemptRepository(session)
    attempt = PaymentAttempt(
        customer_id=persisted_customer.id,
        razorpay_payment_id="pay_roundtrip_test_001",
        razorpay_order_id="order_roundtrip_001",
        attempt_sequence=1,
        amount_subunits=150000,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        status=AttemptStatus.FAILED,
        error_code="BAD_REQUEST_ERROR",
        gateway_created_at=datetime.now(timezone.utc),
    )
    created = repo.create(attempt)
    assert created.id == attempt.id

    by_id = repo.get_by_id(attempt.id)
    assert by_id is not None
    assert by_id.razorpay_payment_id == "pay_roundtrip_test_001"
    assert by_id.amount_subunits == 150000

    by_pay_id = repo.get_by_payment_id("pay_roundtrip_test_001")
    assert by_pay_id is not None
    assert by_pay_id.id == attempt.id


def test_candidate_and_decision_repository_roundtrip(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Test CandidateRepository and DecisionRepository persistence."""
    opp_repo = OpportunityRepository(session)
    opp, _ = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    cand_repo = CandidateRepository(session)
    candidate = InterventionCandidate(
        opportunity_id=opp.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        parameters={"template": "sms_retry_1"},
        p_recovery=0.45,
        p_organic=0.20,
        direct_cost_subunits=5000,
        friction_cost_subunits=1000,
        risk_penalty_subunits=0,
        expected_net_value_subunits=25000,
        confidence_score=0.92,
        contact_fatigue=0.5,
    )
    created_cand = cand_repo.create(candidate)
    assert created_cand.id == candidate.id

    cand_list = cand_repo.list_by_opportunity_id(opp.id)
    assert len(cand_list) == 1
    assert cand_list[0].id == candidate.id

    dec_repo = DecisionRepository(session)
    decision = RecoveryDecision(
        opportunity_id=opp.id,
        selected_candidate_id=candidate.id,
        decision_type=DecisionType.AUTHORIZED,
        policy_evaluation_details={"checks": ["margin_ok", "fatigue_ok"]},
        explanation="Dispatch SMS payment link.",
    )
    created_dec = dec_repo.create(decision)
    assert created_dec.id == decision.id

    by_id = dec_repo.get_by_id(decision.id)
    assert by_id is not None
    assert by_id.selected_candidate_id == candidate.id

    by_opp = dec_repo.get_by_opportunity_id(opp.id)
    assert by_opp is not None
    assert by_opp.id == decision.id


def test_execution_record_repository_roundtrip(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Test ExecutionRecordRepository create_voucher, get, and update."""
    opp_repo = OpportunityRepository(session)
    opp, _ = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

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

    exec_repo = ExecutionRecordRepository(session)
    voucher = ExecutionRecord(
        decision_id=decision.id,
        attempt_index=1,
        idempotency_key="idemp_key_unique_voucher_001",
        reference_id="ref_id_unique_voucher_001",
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        execution_status=ExecutionStatus.CLAIMED,
    )
    created_voucher = exec_repo.create_voucher(voucher)
    assert created_voucher.id == voucher.id

    by_key = exec_repo.get_by_idempotency_key("idemp_key_unique_voucher_001")
    assert by_key is not None
    assert by_key.id == voucher.id

    by_ref = exec_repo.get_by_reference_id("ref_id_unique_voucher_001")
    assert by_ref is not None
    assert by_ref.id == voucher.id

    # Update voucher to EXECUTED
    by_key.execution_status = ExecutionStatus.EXECUTED
    by_key.external_reference_id = "rzp_offer_id_123"
    by_key.executed_at = datetime.now(timezone.utc)
    updated_voucher = exec_repo.update(by_key)
    assert updated_voucher.execution_status == ExecutionStatus.EXECUTED
    assert updated_voucher.external_reference_id == "rzp_offer_id_123"


def test_audit_event_repository_roundtrip(session: Session, persisted_merchant: Merchant) -> None:
    """Test AuditEventRepository append and multi-tenant queries."""
    repo = AuditEventRepository(session)
    aggregate_id = uuid.uuid4()
    trace_id = "trace_abc_123"

    event1 = AuditEvent(
        merchant_id=persisted_merchant.id,
        trace_id=trace_id,
        aggregate_type="RecoveryOpportunity",
        aggregate_id=aggregate_id,
        event_name="OPPORTUNITY_OPENED",
        actor_type=ActorType.SYSTEM,
        metadata={"reason": "payment_failure"},
    )
    repo.record_event(event1)

    event2 = AuditEvent(
        merchant_id=persisted_merchant.id,
        trace_id=trace_id,
        aggregate_type="RecoveryOpportunity",
        aggregate_id=aggregate_id,
        event_name="OPPORTUNITY_EVALUATED",
        actor_type=ActorType.POLICY_GATE,
        metadata={"decision": "AUTHORIZED"},
    )
    repo.record_event(event2)

    by_agg = repo.list_by_aggregate("RecoveryOpportunity", aggregate_id)
    assert len(by_agg) == 2
    assert by_agg[0].event_name == "OPPORTUNITY_OPENED"
    assert by_agg[1].event_name == "OPPORTUNITY_EVALUATED"

    by_trace = repo.list_by_trace(trace_id)
    assert len(by_trace) == 2

    by_merchant = repo.list_by_merchant(persisted_merchant.id)
    assert len(by_merchant) >= 2


def test_policy_rule_repository_roundtrip(session: Session, persisted_merchant: Merchant) -> None:
    """Test PolicyRuleRepository create, list_active, and deactivate."""
    repo = PolicyRuleRepository(session)
    rule = PolicyRule(
        merchant_id=persisted_merchant.id,
        rule_type=RuleType.QUIET_HOURS,
        parameters={"quiet_start": "22:00", "quiet_end": "08:00"},
        is_active=True,
    )
    created = repo.create(rule)
    assert created.id == rule.id

    active_rules = repo.list_active_by_merchant(persisted_merchant.id)
    assert len(active_rules) == 1
    assert active_rules[0].id == rule.id

    # Deactivate
    deactivated = repo.deactivate(rule.id)
    assert not deactivated.is_active

    active_rules_after = repo.list_active_by_merchant(persisted_merchant.id)
    assert len(active_rules_after) == 0


def test_payment_evidence_repository_roundtrip(
    session: Session,
    persisted_merchant: Merchant,
    persisted_customer: Customer,
    sample_attempt: PaymentAttempt,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    """Test PaymentEvidenceRepository create and lookup."""
    opp_repo = OpportunityRepository(session)
    opp, _ = opp_repo.create_with_initial_attempt(sample_opportunity, sample_attempt)

    repo = PaymentEvidenceRepository(session)
    evidence = PaymentEvidence(
        opportunity_id=opp.id,
        razorpay_payment_id="pay_evidence_test_001",
        event_type="payment.captured",
        signature_hash="sha256_mock_hash_xyz",
        captured_amount_subunits=sample_attempt.amount_subunits,
    )
    created = repo.create(evidence)
    assert created.id == evidence.id

    by_pay = repo.get_by_payment_id("pay_evidence_test_001")
    assert by_pay is not None
    assert by_pay.signature_hash == "sha256_mock_hash_xyz"

    by_opp = repo.list_by_opportunity_id(opp.id)
    assert len(by_opp) == 1
    assert by_opp[0].id == evidence.id
