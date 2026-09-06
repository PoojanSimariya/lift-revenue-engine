"""Bidirectional mapping between pure domain models and SQLAlchemy ORM entities."""

from __future__ import annotations

from lift.core.types import (
    ActorType,
    AttemptStatus,
    DecisionType,
    ExecutionStatus,
    FailureCategory,
    InterventionType,
    OpportunityState,
    OrganicEstimationSource,
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
from lift.storage.orm_models import (
    AuditEventORM,
    CustomerORM,
    ExecutionRecordORM,
    InterventionCandidateORM,
    MerchantORM,
    PaymentAttemptORM,
    PaymentEvidenceORM,
    PolicyRuleORM,
    RecoveryDecisionORM,
    RecoveryOpportunityORM,
)


# 1. Merchant
def to_merchant_orm(domain: Merchant) -> MerchantORM:
    return MerchantORM(
        id=domain.id,
        name=domain.name,
        default_currency=domain.default_currency,
        timezone=domain.timezone,
        idempotency_salt=domain.idempotency_salt,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


def to_merchant_domain(orm: MerchantORM) -> Merchant:
    return Merchant(
        id=orm.id,
        name=orm.name,
        default_currency=orm.default_currency,
        timezone=orm.timezone,
        idempotency_salt=orm.idempotency_salt,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


# 2. Customer
def to_customer_orm(domain: Customer) -> CustomerORM:
    return CustomerORM(
        id=domain.id,
        merchant_id=domain.merchant_id,
        external_customer_id=domain.external_customer_id,
        phone_hash=domain.phone_hash,
        email_hash=domain.email_hash,
        risk_tier=domain.risk_tier,
        lifetime_recovery_count=domain.lifetime_recovery_count,
        lifetime_failure_count=domain.lifetime_failure_count,
        rolling_contacts_7d=domain.rolling_contacts_7d,
        last_contacted_at=domain.last_contacted_at,
        created_at=domain.created_at,
    )


def to_customer_domain(orm: CustomerORM) -> Customer:
    return Customer(
        id=orm.id,
        merchant_id=orm.merchant_id,
        external_customer_id=orm.external_customer_id,
        phone_hash=orm.phone_hash,
        email_hash=orm.email_hash,
        risk_tier=orm.risk_tier,
        lifetime_recovery_count=orm.lifetime_recovery_count,
        lifetime_failure_count=orm.lifetime_failure_count,
        rolling_contacts_7d=orm.rolling_contacts_7d,
        last_contacted_at=orm.last_contacted_at,
        created_at=orm.created_at,
    )


# 3. PolicyRule
def to_policy_rule_orm(domain: PolicyRule) -> PolicyRuleORM:
    return PolicyRuleORM(
        id=domain.id,
        merchant_id=domain.merchant_id,
        rule_type=domain.rule_type.value,
        parameters=domain.parameters,
        is_active=domain.is_active,
        created_at=domain.created_at,
    )


def to_policy_rule_domain(orm: PolicyRuleORM) -> PolicyRule:
    return PolicyRule(
        id=orm.id,
        merchant_id=orm.merchant_id,
        rule_type=RuleType(orm.rule_type),
        parameters=orm.parameters,
        is_active=orm.is_active,
        created_at=orm.created_at,
    )


# 4. PaymentAttempt
def to_attempt_orm(domain: PaymentAttempt) -> PaymentAttemptORM:
    return PaymentAttemptORM(
        id=domain.id,
        customer_id=domain.customer_id,
        recovery_opportunity_id=domain.recovery_opportunity_id,
        razorpay_payment_id=domain.razorpay_payment_id,
        razorpay_order_id=domain.razorpay_order_id,
        attempt_sequence=domain.attempt_sequence,
        amount_subunits=domain.amount_subunits,
        currency=domain.currency,
        payment_method=domain.payment_method.value,
        status=domain.status.value,
        error_code=domain.error_code,
        error_description=domain.error_description,
        error_source=domain.error_source,
        error_step=domain.error_step,
        error_reason=domain.error_reason,
        gateway_created_at=domain.gateway_created_at,
        raw_payload=domain.raw_payload,
        ingested_at=domain.ingested_at,
    )


def to_attempt_domain(orm: PaymentAttemptORM) -> PaymentAttempt:
    return PaymentAttempt(
        id=orm.id,
        customer_id=orm.customer_id,
        recovery_opportunity_id=orm.recovery_opportunity_id,
        razorpay_payment_id=orm.razorpay_payment_id,
        razorpay_order_id=orm.razorpay_order_id,
        attempt_sequence=orm.attempt_sequence,
        amount_subunits=orm.amount_subunits,
        currency=orm.currency,
        payment_method=PaymentMethod(orm.payment_method),
        status=AttemptStatus(orm.status),
        error_code=orm.error_code,
        error_description=orm.error_description,
        error_source=orm.error_source,
        error_step=orm.error_step,
        error_reason=orm.error_reason,
        gateway_created_at=orm.gateway_created_at,
        raw_payload=orm.raw_payload,
        ingested_at=orm.ingested_at,
    )


# 5. RecoveryOpportunity
def to_opportunity_orm(domain: RecoveryOpportunity) -> RecoveryOpportunityORM:
    return RecoveryOpportunityORM(
        id=domain.id,
        merchant_id=domain.merchant_id,
        customer_id=domain.customer_id,
        order_id=domain.order_id,
        initial_attempt_id=domain.initial_attempt_id,
        latest_attempt_id=domain.latest_attempt_id,
        amount_at_risk_subunits=domain.amount_at_risk_subunits,
        currency=domain.currency,
        current_state=domain.current_state.value,
        failure_category=domain.failure_category.value,
        organic_recovery_estimate=float(domain.organic_recovery_estimate),
        organic_estimation_source=domain.organic_estimation_source.value,
        failure_attempt_count=domain.failure_attempt_count,
        total_interventions_count=domain.total_interventions_count,
        total_contacts_count=domain.total_contacts_count,
        version=domain.version,
        opened_at=domain.opened_at,
        closed_at=domain.closed_at,
        last_evaluated_at=domain.last_evaluated_at,
        execution_claimed_at=domain.execution_claimed_at,
    )


def to_opportunity_domain(orm: RecoveryOpportunityORM) -> RecoveryOpportunity:
    return RecoveryOpportunity(
        id=orm.id,
        merchant_id=orm.merchant_id,
        customer_id=orm.customer_id,
        order_id=orm.order_id,
        initial_attempt_id=orm.initial_attempt_id,
        latest_attempt_id=orm.latest_attempt_id,
        amount_at_risk_subunits=orm.amount_at_risk_subunits,
        currency=orm.currency,
        current_state=OpportunityState(orm.current_state),
        failure_category=FailureCategory(orm.failure_category),
        organic_recovery_estimate=float(orm.organic_recovery_estimate),
        organic_estimation_source=OrganicEstimationSource(orm.organic_estimation_source),
        failure_attempt_count=orm.failure_attempt_count,
        total_interventions_count=orm.total_interventions_count,
        total_contacts_count=orm.total_contacts_count,
        version=orm.version,
        opened_at=orm.opened_at,
        closed_at=orm.closed_at,
        last_evaluated_at=orm.last_evaluated_at,
        execution_claimed_at=orm.execution_claimed_at,
    )


# 6. InterventionCandidate
def to_candidate_orm(domain: InterventionCandidate) -> InterventionCandidateORM:
    return InterventionCandidateORM(
        id=domain.id,
        opportunity_id=domain.opportunity_id,
        intervention_type=domain.intervention_type.value,
        parameters=domain.parameters,
        p_recovery=float(domain.p_recovery),
        p_organic=float(domain.p_organic),
        direct_cost_subunits=domain.direct_cost_subunits,
        friction_cost_subunits=domain.friction_cost_subunits,
        risk_penalty_subunits=domain.risk_penalty_subunits,
        expected_net_value_subunits=domain.expected_net_value_subunits,
        confidence_score=float(domain.confidence_score),
        contact_fatigue=float(domain.contact_fatigue),
        generated_at=domain.generated_at,
    )


def to_candidate_domain(orm: InterventionCandidateORM) -> InterventionCandidate:
    return InterventionCandidate(
        id=orm.id,
        opportunity_id=orm.opportunity_id,
        intervention_type=InterventionType(orm.intervention_type),
        parameters=orm.parameters,
        p_recovery=float(orm.p_recovery),
        p_organic=float(orm.p_organic),
        direct_cost_subunits=orm.direct_cost_subunits,
        friction_cost_subunits=orm.friction_cost_subunits,
        risk_penalty_subunits=orm.risk_penalty_subunits,
        expected_net_value_subunits=orm.expected_net_value_subunits,
        confidence_score=float(orm.confidence_score),
        contact_fatigue=float(orm.contact_fatigue),
        generated_at=orm.generated_at,
    )


# 7. RecoveryDecision
def to_decision_orm(domain: RecoveryDecision) -> RecoveryDecisionORM:
    return RecoveryDecisionORM(
        id=domain.id,
        opportunity_id=domain.opportunity_id,
        selected_candidate_id=domain.selected_candidate_id,
        decision_type=domain.decision_type.value,
        policy_evaluation_details=domain.policy_evaluation_details,
        blocked_reason_code=domain.blocked_reason_code,
        explanation=domain.explanation,
        decided_at=domain.decided_at,
    )


def to_decision_domain(orm: RecoveryDecisionORM) -> RecoveryDecision:
    return RecoveryDecision(
        id=orm.id,
        opportunity_id=orm.opportunity_id,
        selected_candidate_id=orm.selected_candidate_id,
        decision_type=DecisionType(orm.decision_type),
        policy_evaluation_details=orm.policy_evaluation_details,
        blocked_reason_code=orm.blocked_reason_code,
        explanation=orm.explanation,
        decided_at=orm.decided_at,
    )


# 8. ExecutionRecord
def to_execution_record_orm(domain: ExecutionRecord) -> ExecutionRecordORM:
    return ExecutionRecordORM(
        id=domain.id,
        decision_id=domain.decision_id,
        attempt_index=domain.attempt_index,
        idempotency_key=domain.idempotency_key,
        reference_id=domain.reference_id,
        intervention_type=domain.intervention_type.value,
        execution_status=domain.execution_status.value,
        external_reference_id=domain.external_reference_id,
        failure_message=domain.failure_message,
        claimed_at=domain.claimed_at,
        executed_at=domain.executed_at,
        task_id=domain.task_id,
        lease_version=domain.lease_version,
    )


def to_execution_record_domain(orm: ExecutionRecordORM) -> ExecutionRecord:
    return ExecutionRecord(
        id=orm.id,
        decision_id=orm.decision_id,
        attempt_index=orm.attempt_index,
        idempotency_key=orm.idempotency_key,
        reference_id=orm.reference_id,
        intervention_type=InterventionType(orm.intervention_type),
        execution_status=ExecutionStatus(orm.execution_status),
        external_reference_id=orm.external_reference_id,
        failure_message=orm.failure_message,
        claimed_at=orm.claimed_at,
        executed_at=orm.executed_at,
        task_id=orm.task_id,
        lease_version=orm.lease_version,
    )


# 9. PaymentEvidence
def to_evidence_orm(domain: PaymentEvidence) -> PaymentEvidenceORM:
    return PaymentEvidenceORM(
        id=domain.id,
        opportunity_id=domain.opportunity_id,
        razorpay_payment_id=domain.razorpay_payment_id,
        event_type=domain.event_type,
        signature_hash=domain.signature_hash,
        captured_amount_subunits=domain.captured_amount_subunits,
        verified_at=domain.verified_at,
    )


def to_evidence_domain(orm: PaymentEvidenceORM) -> PaymentEvidence:
    return PaymentEvidence(
        id=orm.id,
        opportunity_id=orm.opportunity_id,
        razorpay_payment_id=orm.razorpay_payment_id,
        event_type=orm.event_type,
        signature_hash=orm.signature_hash,
        captured_amount_subunits=orm.captured_amount_subunits,
        verified_at=orm.verified_at,
    )


# 10. AuditEvent
def to_audit_orm(domain: AuditEvent) -> AuditEventORM:
    return AuditEventORM(
        id=domain.id,
        merchant_id=domain.merchant_id,
        trace_id=domain.trace_id,
        aggregate_type=domain.aggregate_type,
        aggregate_id=domain.aggregate_id,
        event_name=domain.event_name,
        state_before=domain.state_before,
        state_after=domain.state_after,
        actor_type=domain.actor_type.value,
        metadata_json=domain.metadata,
        created_at=domain.created_at,
    )


def to_audit_domain(orm: AuditEventORM) -> AuditEvent:
    return AuditEvent(
        id=orm.id,
        merchant_id=orm.merchant_id,
        trace_id=orm.trace_id,
        aggregate_type=orm.aggregate_type,
        aggregate_id=orm.aggregate_id,
        event_name=orm.event_name,
        state_before=orm.state_before,
        state_after=orm.state_after,
        actor_type=ActorType(orm.actor_type),
        metadata=orm.metadata_json or {},
        created_at=orm.created_at,
    )
