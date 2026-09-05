"""Unit tests verifying service responsibility boundaries, lifecycle transitions, and decoupling."""

from datetime import datetime, timezone
from uuid import uuid4

from lift.core.types import DecisionType, FailureCategory, InterventionType, OpportunityState
from lift.domain.models import (
    Customer,
    InterventionCandidate,
    Merchant,
    PaymentAttempt,
    RecoveryOpportunity,
)
from lift.services.evaluation import InterventionEvaluationService
from lift.services.lifecycle import OpportunityLifecycleService
from lift.services.policy_gate import PolicyGateService


def test_lifecycle_service_opportunity_creation_and_association(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_attempt: PaymentAttempt,
) -> None:
    # Test pure domain opportunity creation from an initial attempt
    opp, linked_attempt = OpportunityLifecycleService.create_opportunity_from_attempt(
        merchant_id=sample_merchant.id,
        customer_id=sample_customer.id,
        order_id="order_test_999",
        initial_attempt=sample_attempt,
        failure_category=FailureCategory.TRANSIENT_NETWORK,
        organic_recovery_estimate=0.40,
    )

    assert opp.order_id == "order_test_999"
    assert opp.initial_attempt_id == sample_attempt.id
    assert opp.latest_attempt_id == sample_attempt.id
    assert opp.current_state == OpportunityState.OPEN
    assert linked_attempt.recovery_opportunity_id == opp.id
    assert opp.failure_attempt_count == 1


def test_lifecycle_service_additional_attempt_association(
    sample_opportunity: RecoveryOpportunity,
    sample_customer: Customer,
) -> None:
    opp = sample_opportunity
    initial_fail_count = opp.failure_attempt_count

    subsequent_attempt = PaymentAttempt(
        customer_id=sample_customer.id,
        razorpay_payment_id="pay_test_subsequent",
        razorpay_order_id=opp.order_id,
        attempt_sequence=2,
        amount_subunits=opp.amount_at_risk_subunits,
        currency=opp.currency,
        payment_method="upi",
        status="failed",
        error_code="INSUFFICIENT_FUNDS",
        gateway_created_at=datetime.now(timezone.utc),
    )

    res = OpportunityLifecycleService.associate_additional_attempt(opp, subsequent_attempt)
    assert not res.transitioned
    assert opp.latest_attempt_id == subsequent_attempt.id
    assert opp.failure_attempt_count == initial_fail_count + 1
    assert subsequent_attempt.recovery_opportunity_id == opp.id

    # Now associate a successful capture attempt
    capture_attempt = PaymentAttempt(
        customer_id=sample_customer.id,
        razorpay_payment_id="pay_test_captured",
        razorpay_order_id=opp.order_id,
        attempt_sequence=3,
        amount_subunits=opp.amount_at_risk_subunits,
        currency=opp.currency,
        payment_method="upi",
        status="captured",
        gateway_created_at=datetime.now(timezone.utc),
    )
    res_cap = OpportunityLifecycleService.associate_additional_attempt(opp, capture_attempt)
    assert res_cap.transitioned
    assert opp.current_state == OpportunityState.RECOVERED


def test_lifecycle_service_synchronous_transitions(
    sample_opportunity: RecoveryOpportunity,
) -> None:
    opp = sample_opportunity
    assert opp.current_state == OpportunityState.OPEN

    # 1. start_evaluation: OPEN -> IN_EVALUATION
    s1 = OpportunityLifecycleService.transition_to_in_evaluation(opp)
    assert s1 == OpportunityState.IN_EVALUATION
    assert opp.current_state == OpportunityState.IN_EVALUATION

    # 2. schedule_action: IN_EVALUATION -> ACTION_SCHEDULED
    s2 = OpportunityLifecycleService.transition_to_action_scheduled(opp)
    assert s2 == OpportunityState.ACTION_SCHEDULED

    # 3. claim_execution: ACTION_SCHEDULED -> ACTION_EXECUTING
    s3 = OpportunityLifecycleService.transition_to_action_executing(opp)
    assert s3 == OpportunityState.ACTION_EXECUTING

    # 4. confirm_dispatch: ACTION_EXECUTING -> AWAITING_SETTLEMENT
    s4 = OpportunityLifecycleService.transition_to_awaiting_settlement(opp)
    assert s4 == OpportunityState.AWAITING_SETTLEMENT

    # 5. record_settlement_captured: AWAITING_SETTLEMENT -> RECOVERED
    res = OpportunityLifecycleService.record_settlement_captured(opp)
    assert res.transitioned
    assert opp.current_state == OpportunityState.RECOVERED


def test_lifecycle_service_terminal_transitions(
    sample_opportunity: RecoveryOpportunity,
) -> None:
    # Test transition_to_expired
    opp1 = sample_opportunity
    OpportunityLifecycleService.transition_to_in_evaluation(opp1)
    OpportunityLifecycleService.transition_to_action_blocked(opp1)
    s_exp = OpportunityLifecycleService.transition_to_expired(opp1)
    assert s_exp == OpportunityState.EXPIRED

    # Test transition_to_terminated
    opp2 = sample_opportunity.model_copy()
    opp2.id = uuid4()
    opp2.current_state = OpportunityState.OPEN
    s_term = OpportunityLifecycleService.transition_to_terminated(opp2, "User cancelled")
    assert s_term == OpportunityState.TERMINATED

    # Test transition_to_escalated_human
    opp3 = sample_opportunity.model_copy()
    opp3.id = uuid4()
    opp3.current_state = OpportunityState.IN_EVALUATION
    s_esc = OpportunityLifecycleService.transition_to_escalated_human(opp3, "High value invoice")
    assert s_esc == OpportunityState.ESCALATED_HUMAN


def test_evaluation_service_does_not_mutate_opportunity(
    sample_opportunity: RecoveryOpportunity,
    sample_customer: Customer,
) -> None:
    eval_service = InterventionEvaluationService()
    initial_state = sample_opportunity.current_state
    initial_version = sample_opportunity.version

    candidates = eval_service.evaluate_all_candidates(
        opportunity=sample_opportunity,
        customer=sample_customer,
        p_recovery_by_type={
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.60,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.70,
            InterventionType.NO_ACTION: 0.30,
        },
        p_organic=0.30,
        confidence_score=0.85,
    )

    assert len(candidates) == len(InterventionType)

    # Invariant: Evaluation service MUST NOT mutate opportunity state or version
    assert sample_opportunity.current_state == initial_state
    assert sample_opportunity.version == initial_version


def test_policy_gate_selects_best_candidate(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    eval_service = InterventionEvaluationService()
    policy_service = PolicyGateService()

    # Daytime evaluation
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=timezone.utc)

    candidates = eval_service.evaluate_all_candidates(
        opportunity=sample_opportunity,
        customer=sample_customer,
        p_recovery_by_type={
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.80,  # Higher NIRV
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.50,
            InterventionType.NO_ACTION: 0.30,
        },
        p_organic=0.30,
        confidence_score=0.90,
        eval_time=daytime,
    )

    decision = policy_service.select_best_candidate(
        candidates=candidates,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.AUTHORIZED
    # Selected candidate should be WhatsApp (highest positive net value)
    selected = next(c for c in candidates if c.id == decision.selected_candidate_id)
    assert selected.intervention_type == InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP


def test_policy_gate_empty_and_fallback_candidates(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    policy_service = PolicyGateService()

    # 1. Empty candidates list -> NO_ACTION with blocked_reason_code NO_CANDIDATES
    d_empty = policy_service.select_best_candidate(
        candidates=[],
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
    )
    assert d_empty.decision_type == DecisionType.NO_ACTION
    assert d_empty.blocked_reason_code == "NO_CANDIDATES"

    # 2. All active candidates blocked due to negative NIRV, NO_ACTION present -> selects NO_ACTION
    neg_candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.10,
        p_organic=0.30,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=500,
        expected_net_value_subunits=-1000,
        confidence_score=0.85,
    )
    no_action = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.NO_ACTION,
        p_recovery=0.30,
        p_organic=0.30,
        direct_cost_subunits=0,
        friction_cost_subunits=0,
        risk_penalty_subunits=0,
        expected_net_value_subunits=0,
        confidence_score=0.90,
    )

    d_fallback = policy_service.select_best_candidate(
        candidates=[neg_candidate, no_action],
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
    )
    assert d_fallback.decision_type == DecisionType.NO_ACTION
    assert d_fallback.selected_candidate_id == no_action.id
