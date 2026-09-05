"""Unit tests for the NIRV economic calculation engine across the 8 canonical scenarios."""

import pytest
from lift.core.errors import DataValidationError
from lift.core.types import InterventionType
from lift.economics.nirv import calculate_nirv


def test_nirv_scenario_1_zero_previous_touches() -> None:
    # 1. Zero previous touches: clean base touch, positive expected recovery
    amount = 500000  # 5,000 INR
    p_rec = 0.60
    p_org = 0.20
    fatigue = 1.0  # Base SMS fatigue (w=1.0, R=0, N=0)
    conf = 0.85

    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        contact_fatigue=fatigue,
        confidence_score=conf,
    )

    # Expected Incremental = (0.60 - 0.20) * 500,000 = 200,000 paise
    assert breakdown.expected_incremental_recovery_subunits == 200000
    assert breakdown.direct_cost_subunits == 25  # SMS cost 25 paise
    # Friction Cost = 0.05 * 500,000 * 1.0 = 25,000 paise
    assert breakdown.friction_cost_subunits == 25000
    # Risk Penalty = 0.10 * (1 - 0.85) * 500,000 = 0.10 * 0.15 * 500,000 = 7,500 paise
    assert breakdown.risk_penalty_subunits == 7500
    # NIRV = 200,000 - 25 - 25,000 - 7,500 = 167,475 paise
    assert breakdown.nirv_subunits == 167475
    assert breakdown.is_positive


def test_nirv_scenario_2_recent_touches_high_fatigue() -> None:
    # 2. Recent touches: high contact fatigue reduces NIRV
    amount = 500000
    p_rec = 0.60
    p_org = 0.20
    fatigue = 3.5  # Elevated fatigue from recent contacts
    conf = 0.85

    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        contact_fatigue=fatigue,
        confidence_score=conf,
    )

    # Friction Cost = 0.05 * 500,000 * 3.5 = 87,500 paise
    assert breakdown.friction_cost_subunits == 87500
    # NIRV = 200,000 - 25 - 87,500 - 7,500 = 104,975 paise
    assert breakdown.nirv_subunits == 104975


def test_nirv_scenario_3_high_friction_coefficient() -> None:
    # 3. High friction coefficient test
    amount = 500000
    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=0.50,
        p_organic=0.40,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        contact_fatigue=2.0,
        confidence_score=0.80,
        lambda_friction=0.10,  # 10% friction
    )
    # Expected incremental = 0.10 * 500,000 = 50,000 paise
    assert breakdown.expected_incremental_recovery_subunits == 50000
    # Friction cost = 0.10 * 500,000 * 2.0 = 100,000 paise
    assert breakdown.friction_cost_subunits == 100000
    # NIRV will be negative due to heavy friction
    assert breakdown.nirv_subunits < 0
    assert not breakdown.is_positive


def test_nirv_scenario_4_micro_ticket_transaction() -> None:
    # 4. Micro-ticket (< 100 INR, e.g. 49 INR = 4,900 paise)
    amount = 4900
    p_rec = 0.25
    p_org = 0.20  # Only 5% incremental lift (245 paise)
    conf = 0.80

    # WhatsApp costs 80 paise, SMS costs 25 paise
    breakdown_wa = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        contact_fatigue=1.5,
        confidence_score=conf,
    )
    # Expected = 0.05 * 4,900 = 245 paise
    assert breakdown_wa.expected_incremental_recovery_subunits == 245
    assert breakdown_wa.direct_cost_subunits == 80
    # Friction = 0.05 * 4,900 * 1.5 = 368 paise
    assert breakdown_wa.friction_cost_subunits == 368
    # NIRV is negative! Paid outreach on micro-ticket correctly suppressed
    assert breakdown_wa.nirv_subunits < 0
    assert not breakdown_wa.is_positive


def test_nirv_scenario_5_large_ticket_transaction() -> None:
    # 5. Large ticket (> 10,000 INR, e.g. 25,000 INR = 2,500,000 paise)
    amount = 2500000
    p_rec = 0.70
    p_org = 0.30
    conf = 0.90

    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        contact_fatigue=1.2,
        confidence_score=conf,
    )
    # Expected Incremental = 0.40 * 2,500,000 = 1,000,000 paise (10,000 INR)
    assert breakdown.expected_incremental_recovery_subunits == 1000000
    assert breakdown.direct_cost_subunits == 80
    # Substantial positive NIRV
    assert breakdown.nirv_subunits > 800000
    assert breakdown.is_positive


def test_nirv_scenario_6_low_confidence_score() -> None:
    # 6. Minimum allowed confidence score (0.50) -> maximum uncertainty (0.50)
    amount = 500000
    p_rec = 0.50
    p_org = 0.30
    conf = 0.50  # Lower bound of calibrated confidence -> uncertainty = 0.50

    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        contact_fatigue=1.0,
        confidence_score=conf,
    )
    # Risk penalty = 0.10 * 0.50 * 500,000 = 25,000 paise
    assert breakdown.risk_penalty_subunits == 25000
    assert breakdown.uncertainty == 0.50


def test_nirv_scenario_7_high_confidence_score() -> None:
    # 7. High confidence score (low risk penalty)
    amount = 500000
    p_rec = 0.50
    p_org = 0.30
    conf = 0.95  # High confidence -> uncertainty = 0.05

    breakdown = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=p_rec,
        p_organic=p_org,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        contact_fatigue=1.0,
        confidence_score=conf,
    )
    # Risk penalty = 0.10 * 0.05 * 500,000 = 2,500 paise
    assert breakdown.risk_penalty_subunits == 2500
    assert breakdown.uncertainty == 0.05


def test_nirv_scenario_8_non_contact_actions() -> None:
    # 8. Non-contact actions: DirectCost = 0, FrictionCost = 0
    amount = 500000

    # A. NO_ACTION
    breakdown_no_action = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=0.30,
        p_organic=0.30,
        intervention_type=InterventionType.NO_ACTION,
        contact_fatigue=5.0,  # Fatigue is ignored for non-contact
        confidence_score=0.90,
    )
    assert breakdown_no_action.direct_cost_subunits == 0
    assert breakdown_no_action.friction_cost_subunits == 0
    assert breakdown_no_action.expected_incremental_recovery_subunits == 0

    # B. INTERNAL_RETRY_SCHEDULE
    breakdown_retry = calculate_nirv(
        amount_at_risk_subunits=amount,
        p_recovery=0.45,
        p_organic=0.30,
        intervention_type=InterventionType.INTERNAL_RETRY_SCHEDULE,
        contact_fatigue=10.0,  # Ignored
        confidence_score=0.90,
    )
    assert breakdown_retry.direct_cost_subunits == 0
    assert breakdown_retry.friction_cost_subunits == 0
    # Expected incremental = 0.15 * 500,000 = 75,000 paise
    assert breakdown_retry.expected_incremental_recovery_subunits == 75000


def test_nirv_input_validation() -> None:
    with pytest.raises(DataValidationError):
        calculate_nirv(-100, 0.5, 0.2, InterventionType.NO_ACTION, 0.0, 0.8)

    with pytest.raises(DataValidationError):
        calculate_nirv(1000, 1.5, 0.2, InterventionType.NO_ACTION, 0.0, 0.8)

    with pytest.raises(DataValidationError):
        calculate_nirv(1000, 0.5, -0.1, InterventionType.NO_ACTION, 0.0, 0.8)

    # Confidence score < 0.50 must be rejected
    with pytest.raises(DataValidationError):
        calculate_nirv(1000, 0.5, 0.2, InterventionType.NO_ACTION, 0.0, 0.49)

    # Confidence score > 1.0 must be rejected
    with pytest.raises(DataValidationError):
        calculate_nirv(1000, 0.5, 0.2, InterventionType.NO_ACTION, 0.0, 1.01)

    # Bool amount_at_risk must be rejected
    with pytest.raises(DataValidationError):
        calculate_nirv(True, 0.5, 0.2, InterventionType.NO_ACTION, 0.0, 0.8)  # type: ignore[arg-type]


def test_nirv_counterfactual_greater_than_recovery_yields_negative_nirv() -> None:
    # When p_organic > p_recovery: negative incremental lift produces negative NIRV
    breakdown = calculate_nirv(
        amount_at_risk_subunits=100000,
        p_recovery=0.20,
        p_organic=0.35,  # Baseline organic exceeds intervention recovery
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        contact_fatigue=1.0,
        confidence_score=0.85,
    )
    # Incremental recovery = (0.20 - 0.35) * 100,000 = -15,000 paise
    assert breakdown.expected_incremental_recovery_subunits == -15000
    assert breakdown.nirv_subunits < 0
    assert not breakdown.is_positive


def test_nirv_int64_boundary_validation() -> None:
    from lift.core.constants import INT64_MAX

    # Valid at INT64_MAX with non-contact intervention
    # (no risk penalty or friction when p_rec == p_org)
    breakdown = calculate_nirv(
        amount_at_risk_subunits=INT64_MAX,
        p_recovery=0.50,
        p_organic=0.50,
        intervention_type=InterventionType.NO_ACTION,
        contact_fatigue=0.0,
        confidence_score=1.0,
    )
    assert breakdown.nirv_subunits == 0

    # Exceeding INT64_MAX must raise DataValidationError
    with pytest.raises(DataValidationError) as exc_info:
        calculate_nirv(
            amount_at_risk_subunits=INT64_MAX + 1,
            p_recovery=0.50,
            p_organic=0.50,
            intervention_type=InterventionType.NO_ACTION,
            contact_fatigue=0.0,
            confidence_score=1.0,
        )
    assert "amount_at_risk_subunits" in str(exc_info.value)


def test_nirv_rejects_unknown_intervention_type() -> None:
    with pytest.raises(DataValidationError) as exc_info:
        calculate_nirv(
            amount_at_risk_subunits=100000,
            p_recovery=0.60,
            p_organic=0.20,
            intervention_type="MAGIC_PAY",
            contact_fatigue=1.0,
            confidence_score=0.80,
        )
    assert "intervention_type" in str(exc_info.value)
