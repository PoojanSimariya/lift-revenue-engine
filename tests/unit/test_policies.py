"""Unit tests for PolicyGateService, quiet hours in merchant timezone, and contact caps."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lift.core.errors import TimeZoneError
from lift.core.types import DecisionType, InterventionType
from lift.domain.models import (
    Customer,
    InterventionCandidate,
    Merchant,
    RecoveryOpportunity,
)
from lift.policies.rules import ContactCapConfig, QuietHoursConfig
from lift.services.policy_gate import PolicyGateService


def test_quiet_hours_exact_boundaries() -> None:
    config = QuietHoursConfig(start_hour=21, end_hour=8)
    tz = "Asia/Kolkata"

    # 1. 20:59:59 IST -> Allowed (Outside quiet window)
    t_20_59_59 = datetime(2026, 9, 5, 20, 59, 59, tzinfo=ZoneInfo(tz))
    assert not config.is_quiet_hour(t_20_59_59, tz)

    # 2. 21:00:00 IST -> Blocked (Quiet hours start)
    t_21_00_00 = datetime(2026, 9, 5, 21, 0, 0, tzinfo=ZoneInfo(tz))
    assert config.is_quiet_hour(t_21_00_00, tz)

    # 3. 23:30:00 IST -> Blocked
    t_23_30_00 = datetime(2026, 9, 5, 23, 30, 0, tzinfo=ZoneInfo(tz))
    assert config.is_quiet_hour(t_23_30_00, tz)

    # 4. 04:00:00 IST -> Blocked
    t_04_00_00 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=ZoneInfo(tz))
    assert config.is_quiet_hour(t_04_00_00, tz)

    # 5. 07:59:59 IST -> Blocked (1 second before quiet hours exit)
    t_07_59_59 = datetime(2026, 9, 6, 7, 59, 59, tzinfo=ZoneInfo(tz))
    assert config.is_quiet_hour(t_07_59_59, tz)

    # 6. 08:00:00 IST -> Allowed (Quiet hours exit)
    t_08_00_00 = datetime(2026, 9, 6, 8, 0, 0, tzinfo=ZoneInfo(tz))
    assert not config.is_quiet_hour(t_08_00_00, tz)

    # 7. 12:00:00 IST -> Allowed
    t_12_00_00 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=ZoneInfo(tz))
    assert not config.is_quiet_hour(t_12_00_00, tz)


def test_quiet_hours_different_timezones() -> None:
    config = QuietHoursConfig(start_hour=21, end_hour=8)

    # UTC time 16:00:00 is 21:30:00 in Asia/Kolkata (+5:30) -> Quiet in Kolkata
    t_utc = datetime(2026, 9, 5, 16, 0, 0, tzinfo=timezone.utc)
    assert config.is_quiet_hour(t_utc, "Asia/Kolkata")

    # But in America/New_York (-4 EDT in Sept), 16:00 UTC is 12:00 EDT (noon) -> Not quiet
    assert not config.is_quiet_hour(t_utc, "America/New_York")


def test_quiet_hours_invalid_timezone() -> None:
    config = QuietHoursConfig()
    t = datetime.now(timezone.utc)
    import pytest

    with pytest.raises(TimeZoneError):
        config.is_quiet_hour(t, "NonExistent/Invalid_Timezone")


def test_contact_cap_boundaries() -> None:
    cap = ContactCapConfig(max_contacts_7d=3)
    assert not cap.is_limit_exceeded(0)
    assert not cap.is_limit_exceeded(1)
    assert not cap.is_limit_exceeded(2)
    assert cap.is_limit_exceeded(3)  # N=3 is blocked
    assert cap.is_limit_exceeded(4)


def test_policy_gate_evaluates_candidate_authorization(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()

    # Create candidate with positive NIRV during daytime (14:00 IST)
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    sample_customer.rolling_contacts_7d = 1

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,  # Positive NIRV
        confidence_score=0.85,
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.AUTHORIZED
    assert decision.blocked_reason_code is None


def test_policy_gate_blocks_quiet_hours(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()

    # Nighttime: 22:30 IST
    nighttime = datetime(2026, 9, 5, 22, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,
        confidence_score=0.85,
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=nighttime,
    )

    assert decision.decision_type == DecisionType.BLOCKED
    assert decision.blocked_reason_code == "BLOCKED_QUIET_HOURS"


def test_policy_gate_blocks_contact_limit(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    # Customer has N=3 contacts already
    sample_customer.rolling_contacts_7d = 3

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,
        confidence_score=0.85,
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.BLOCKED
    assert decision.blocked_reason_code == "BLOCKED_CONTACT_LIMIT"


def test_policy_gate_non_contact_bypasses_quiet_hours(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    nighttime = datetime(2026, 9, 5, 23, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    # INTERNAL_RETRY_SCHEDULE is non-contact -> allowed even during quiet hours
    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.INTERNAL_RETRY_SCHEDULE,
        p_recovery=0.50,
        p_organic=0.30,
        direct_cost_subunits=0,
        friction_cost_subunits=0,
        risk_penalty_subunits=100,
        expected_net_value_subunits=2000,
        confidence_score=0.85,
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=nighttime,
    )

    assert decision.decision_type == DecisionType.AUTHORIZED
    assert decision.blocked_reason_code is None


def test_policy_gate_contact_fatigue_below_threshold(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    sample_customer.rolling_contacts_7d = 1

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,
        confidence_score=0.85,
        contact_fatigue=3.99,  # Just below suppression threshold
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.AUTHORIZED
    assert decision.blocked_reason_code is None


def test_policy_gate_contact_fatigue_exact_threshold(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    sample_customer.rolling_contacts_7d = 1

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=25,
        friction_cost_subunits=500,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,
        confidence_score=0.85,
        contact_fatigue=4.00,  # Exactly at suppression threshold
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.BLOCKED
    assert decision.blocked_reason_code == "BLOCKED_CONTACT_FATIGUE"
    assert "meets or exceeds" in decision.explanation


def test_policy_gate_contact_fatigue_above_threshold(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    sample_customer.rolling_contacts_7d = 1

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        p_recovery=0.60,
        p_organic=0.20,
        direct_cost_subunits=80,
        friction_cost_subunits=1000,
        risk_penalty_subunits=200,
        expected_net_value_subunits=1500,
        confidence_score=0.85,
        contact_fatigue=4.50,  # Above suppression threshold
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.BLOCKED
    assert decision.blocked_reason_code == "BLOCKED_CONTACT_FATIGUE"


def test_policy_gate_non_contact_bypasses_fatigue(
    sample_merchant: Merchant,
    sample_customer: Customer,
    sample_opportunity: RecoveryOpportunity,
) -> None:
    service = PolicyGateService()
    daytime = datetime(2026, 9, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    candidate = InterventionCandidate(
        opportunity_id=sample_opportunity.id,
        intervention_type=InterventionType.INTERNAL_RETRY_SCHEDULE,
        p_recovery=0.50,
        p_organic=0.30,
        direct_cost_subunits=0,
        friction_cost_subunits=0,
        risk_penalty_subunits=100,
        expected_net_value_subunits=2000,
        confidence_score=0.85,
        contact_fatigue=5.0,  # Non-contact ignores fatigue
    )

    decision = service.evaluate_candidate(
        candidate=candidate,
        opportunity=sample_opportunity,
        merchant=sample_merchant,
        customer=sample_customer,
        eval_time=daytime,
    )

    assert decision.decision_type == DecisionType.AUTHORIZED
    assert decision.blocked_reason_code is None
