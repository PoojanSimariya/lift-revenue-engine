"""Unit tests for domain model validations, INT64 bounds, datetime timezone awareness,
and enum restrictions.
"""

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from lift.core.constants import INT64_MAX
from lift.core.errors import DataValidationError
from lift.core.types import (
    AttemptStatus,
    FailureCategory,
    InterventionType,
    OpportunityState,
    OrganicEstimationSource,
    PaymentMethod,
)
from lift.domain.models import (
    Customer,
    InterventionCandidate,
    PaymentAttempt,
    RecoveryOpportunity,
)


def test_recovery_opportunity_amount_at_risk_int64_max_boundary() -> None:
    # 1. Exact INT64_MAX is accepted
    opp = RecoveryOpportunity(
        merchant_id=uuid4(),
        customer_id=uuid4(),
        order_id="order_boundary_max",
        initial_attempt_id=uuid4(),
        latest_attempt_id=uuid4(),
        amount_at_risk_subunits=INT64_MAX,
        currency="INR",
        current_state=OpportunityState.OPEN,
        failure_category=FailureCategory.TRANSIENT_NETWORK,
        organic_recovery_estimate=0.40,
        organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
    )
    assert opp.amount_at_risk_subunits == INT64_MAX

    # 2. INT64_MAX + 1 is rejected
    with pytest.raises(DataValidationError) as exc_info:
        RecoveryOpportunity(
            merchant_id=uuid4(),
            customer_id=uuid4(),
            order_id="order_boundary_exceeded",
            initial_attempt_id=uuid4(),
            latest_attempt_id=uuid4(),
            amount_at_risk_subunits=INT64_MAX + 1,
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.40,
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
        )
    assert "amount_at_risk_subunits" in str(exc_info.value)
    assert "INT64_MAX" in str(exc_info.value)

    # 3. Negative amount is rejected
    with pytest.raises(DataValidationError) as exc_info:
        RecoveryOpportunity(
            merchant_id=uuid4(),
            customer_id=uuid4(),
            order_id="order_negative",
            initial_attempt_id=uuid4(),
            latest_attempt_id=uuid4(),
            amount_at_risk_subunits=-1,
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.40,
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
        )
    assert "amount_at_risk_subunits" in str(exc_info.value)

    # 4. Bool is rejected
    with pytest.raises(DataValidationError) as exc_info:
        RecoveryOpportunity(
            merchant_id=uuid4(),
            customer_id=uuid4(),
            order_id="order_bool",
            initial_attempt_id=uuid4(),
            latest_attempt_id=uuid4(),
            amount_at_risk_subunits=True,  # type: ignore[arg-type]
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.40,
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
        )
    assert "amount_at_risk_subunits" in str(exc_info.value)

    # 5. Non-integer (float) is rejected
    with pytest.raises(DataValidationError) as exc_info:
        RecoveryOpportunity(
            merchant_id=uuid4(),
            customer_id=uuid4(),
            order_id="order_float",
            initial_attempt_id=uuid4(),
            latest_attempt_id=uuid4(),
            amount_at_risk_subunits=100.5,  # type: ignore[arg-type]
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.40,
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
        )
    assert "amount_at_risk_subunits" in str(exc_info.value)


def test_customer_last_contacted_at_timezone_validation() -> None:
    # 1. Aware UTC is accepted
    aware_utc = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    c1 = Customer(
        merchant_id=uuid4(),
        external_customer_id="cust_ext_1",
        phone_hash="hash1",
        email_hash="hash2",
        last_contacted_at=aware_utc,
    )
    assert c1.last_contacted_at == aware_utc

    # 2. Aware non-UTC is accepted
    aware_non_utc = datetime(2026, 9, 5, 17, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    c2 = Customer(
        merchant_id=uuid4(),
        external_customer_id="cust_ext_2",
        phone_hash="hash1",
        email_hash="hash2",
        last_contacted_at=aware_non_utc,
    )
    assert c2.last_contacted_at == aware_non_utc

    # 3. Naive datetime is strictly rejected (never silently assumed UTC)
    naive_dt = datetime(2026, 9, 5, 12, 0, 0)
    with pytest.raises(DataValidationError) as exc_info:
        Customer(
            merchant_id=uuid4(),
            external_customer_id="cust_ext_3",
            phone_hash="hash1",
            email_hash="hash2",
            last_contacted_at=naive_dt,
        )
    assert "last_contacted_at" in str(exc_info.value)
    assert "timezone-aware" in str(exc_info.value)


def test_strict_enum_validation() -> None:
    # 1. PaymentAttempt strict PaymentMethod
    with pytest.raises(Exception):  # pydantic ValidationError
        PaymentAttempt(
            customer_id=uuid4(),
            razorpay_payment_id="pay_1",
            razorpay_order_id="order_1",
            attempt_sequence=1,
            amount_subunits=1000,
            currency="INR",
            payment_method="unsupported_crypto",  # type: ignore[arg-type]
            status=AttemptStatus.FAILED,
            gateway_created_at=datetime.now(timezone.utc),
        )

    # 2. PaymentAttempt strict AttemptStatus
    with pytest.raises(Exception):
        PaymentAttempt(
            customer_id=uuid4(),
            razorpay_payment_id="pay_1",
            razorpay_order_id="order_1",
            attempt_sequence=1,
            amount_subunits=1000,
            currency="INR",
            payment_method=PaymentMethod.CARD,
            status="pending_something",  # type: ignore[arg-type]
            gateway_created_at=datetime.now(timezone.utc),
        )

    # 3. RecoveryOpportunity strict FailureCategory
    with pytest.raises(Exception):
        RecoveryOpportunity(
            merchant_id=uuid4(),
            customer_id=uuid4(),
            order_id="order_1",
            initial_attempt_id=uuid4(),
            latest_attempt_id=uuid4(),
            amount_at_risk_subunits=1000,
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category="UNKNOWN_FAILURE_CATEGORY",  # type: ignore[arg-type]
            organic_recovery_estimate=0.40,
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
        )


def test_intervention_candidate_confidence_bounds() -> None:
    opp_id = uuid4()

    # 1. 0.50 lower bound accepted
    c_low = InterventionCandidate(
        opportunity_id=opp_id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=100,
        risk_penalty_subunits=100,
        expected_net_value_subunits=1000,
        confidence_score=0.50,
        contact_fatigue=1.5,
    )
    assert c_low.confidence_score == 0.50
    assert c_low.contact_fatigue == 1.5

    # 2. 1.0 upper bound accepted
    c_high = InterventionCandidate(
        opportunity_id=opp_id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=100,
        risk_penalty_subunits=100,
        expected_net_value_subunits=1000,
        confidence_score=1.00,
        contact_fatigue=2.0,
    )
    assert c_high.confidence_score == 1.00

    # 3. < 0.50 rejected
    with pytest.raises(DataValidationError) as exc_info:
        InterventionCandidate(
            opportunity_id=opp_id,
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            p_recovery=0.60,
            p_organic=0.20,
            direct_cost_subunits=25,
            friction_cost_subunits=100,
            risk_penalty_subunits=100,
            expected_net_value_subunits=1000,
            confidence_score=0.49,
        )
    assert "confidence_score" in str(exc_info.value)

    # 4. > 1.0 rejected
    with pytest.raises(DataValidationError) as exc_info:
        InterventionCandidate(
            opportunity_id=opp_id,
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            p_recovery=0.60,
            p_organic=0.20,
            direct_cost_subunits=25,
            friction_cost_subunits=100,
            risk_penalty_subunits=100,
            expected_net_value_subunits=1000,
            confidence_score=1.01,
        )
    assert "confidence_score" in str(exc_info.value)
