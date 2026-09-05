"""Unit tests for Contact Fatigue formulation, recency decay, and suppression thresholds."""

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from lift.core.errors import DataValidationError
from lift.core.types import InterventionType
from lift.economics.fatigue import (
    calculate_contact_fatigue,
    get_channel_weight,
    is_fatigue_suppressed,
)


def test_fatigue_channel_weights() -> None:
    assert get_channel_weight("DIRECT_PAYMENT_LINK_WHATSAPP") == 1.5
    assert get_channel_weight(InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP) == 1.5
    assert get_channel_weight("DIRECT_PAYMENT_LINK_SMS") == 1.0
    assert get_channel_weight(InterventionType.DIRECT_PAYMENT_LINK_SMS) == 1.0
    assert get_channel_weight("DIRECT_PAYMENT_LINK_EMAIL") == 0.4
    assert get_channel_weight(InterventionType.DIRECT_PAYMENT_LINK_EMAIL) == 0.4
    assert get_channel_weight("CUSTOM_WEBHOOK_OUTREACH") == 0.8
    assert get_channel_weight(InterventionType.CUSTOM_WEBHOOK_OUTREACH) == 0.8

    # Non-contact interventions return 0.0 weight
    assert get_channel_weight(InterventionType.NO_ACTION) == 0.0
    assert get_channel_weight(InterventionType.INTERNAL_RETRY_SCHEDULE) == 0.0

    # Unknown intervention types must be rejected, not defaulted
    with pytest.raises(DataValidationError):
        get_channel_weight("UNKNOWN_CHANNEL")

    with pytest.raises(DataValidationError):
        get_channel_weight("custom_whatsapp_action")


def test_fatigue_non_contact_actions() -> None:
    # Non-contact actions always produce 0.0 fatigue regardless of customer contact history
    assert (
        calculate_contact_fatigue(InterventionType.NO_ACTION, 10, datetime.now(timezone.utc)) == 0.0
    )
    assert (
        calculate_contact_fatigue(
            InterventionType.INTERNAL_RETRY_SCHEDULE, 5, datetime.now(timezone.utc)
        )
        == 0.0
    )


def test_fatigue_first_contact_null_timestamp() -> None:
    # When last_contacted_at is None and N=0: Fatigue = w(a) * (1.0 + 0 + 0) = w(a)
    fatigue_sms = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=0,
        last_contacted_at=None,
    )
    assert fatigue_sms == 1.0

    fatigue_wa = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        rolling_contacts_7d=0,
        last_contacted_at=None,
    )
    assert fatigue_wa == 1.5

    fatigue_email = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_EMAIL,
        rolling_contacts_7d=0,
        last_contacted_at=None,
    )
    assert fatigue_email == 0.4


def test_fatigue_48h_half_life_decay() -> None:
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

    # Exactly 48 hours ago: R = exp(-48/48) = exp(-1) = 0.367879...
    t_48h_ago = now - timedelta(hours=48)
    expected_r = math.exp(-1.0)
    expected_fatigue_sms = round(1.0 * (1.0 + expected_r + 0.0), 4)

    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=0,
        last_contacted_at=t_48h_ago,
        current_time=now,
    )
    assert fatigue == expected_fatigue_sms
    assert fatigue == 1.3679


def test_fatigue_2h_recency() -> None:
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    t_2h_ago = now - timedelta(hours=2)

    # R = exp(-2/48) = exp(-0.041666...) = 0.959189...
    expected_r = math.exp(-2.0 / 48.0)
    expected_fatigue = round(1.0 * (1.0 + expected_r + 0.0), 4)

    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=0,
        last_contacted_at=t_2h_ago,
        current_time=now,
    )
    assert fatigue == expected_fatigue
    assert fatigue == 1.9592


def test_fatigue_contact_count_scaling() -> None:
    # N=2: Fatigue = w(a) * (1.0 + R + 0.5 * 2) = w(a) * (2.0 + R)
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    t_48h_ago = now - timedelta(hours=48)

    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=2,
        last_contacted_at=t_48h_ago,
        current_time=now,
    )
    # 1.0 * (1.0 + exp(-1) + 1.0) = 2.0 + 0.3679 = 2.3679
    assert fatigue == 2.3679


def test_fatigue_suppression_threshold() -> None:
    # Threshold is 4.0
    assert not is_fatigue_suppressed(3.99)
    assert is_fatigue_suppressed(4.00)
    assert is_fatigue_suppressed(4.50)

    # When N=4 on WhatsApp: 1.2 * (1.0 + 0.0 + 2.0) = 3.6 (not suppressed without recency)
    # With recent contact 1h ago: 1.2 * (1.0 + 0.979 + 2.0) = 1.2 * 3.979 = 4.775 -> Suppressed!
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    t_1h = now - timedelta(hours=1)
    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
        rolling_contacts_7d=4,
        last_contacted_at=t_1h,
        current_time=now,
    )
    assert fatigue >= 4.0
    assert is_fatigue_suppressed(fatigue)


def test_fatigue_timezone_aware_alignment() -> None:
    # Ensure comparing a UTC time with an Asia/Kolkata aware time works cleanly
    tz_kolkata = ZoneInfo("Asia/Kolkata")
    now_utc = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

    # 2 hours before in local Kolkata time
    t_kolkata = (now_utc - timedelta(hours=2)).astimezone(tz_kolkata)

    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=0,
        last_contacted_at=t_kolkata,
        current_time=now_utc,
    )
    assert fatigue == 1.9592


def test_fatigue_clock_skew_safe() -> None:
    # If last_contacted_at is in the future due to slight clock skew, delta_hours clamped to 0.0
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    future_last = now + timedelta(seconds=30)

    fatigue = calculate_contact_fatigue(
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        rolling_contacts_7d=0,
        last_contacted_at=future_last,
        current_time=now,
    )
    # exp(0) = 1.0 -> 1.0 * (1.0 + 1.0 + 0) = 2.0
    assert fatigue == 2.0


def test_fatigue_rejects_naive_datetime() -> None:
    aware_now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    naive_dt = datetime(2026, 9, 5, 12, 0, 0)

    # Naive current_time must be rejected
    with pytest.raises(DataValidationError) as exc_info:
        calculate_contact_fatigue(
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            rolling_contacts_7d=0,
            last_contacted_at=aware_now,
            current_time=naive_dt,
        )
    assert "current_time" in str(exc_info.value)
    assert "timezone-aware" in str(exc_info.value)

    # Naive last_contacted_at must be rejected
    with pytest.raises(DataValidationError) as exc_info:
        calculate_contact_fatigue(
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            rolling_contacts_7d=0,
            last_contacted_at=naive_dt,
            current_time=aware_now,
        )
    assert "last_contacted_at" in str(exc_info.value)
    assert "timezone-aware" in str(exc_info.value)


def test_fatigue_rejects_unknown_intervention_type() -> None:
    aware_now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(DataValidationError) as exc_info:
        calculate_contact_fatigue(
            intervention_type="TELEPATHY",
            rolling_contacts_7d=0,
            last_contacted_at=aware_now,
            current_time=aware_now,
        )
    assert "intervention_type" in str(exc_info.value)


def test_fatigue_rejects_negative_rolling_contacts() -> None:
    aware_now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(DataValidationError) as exc_info:
        calculate_contact_fatigue(
            intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
            rolling_contacts_7d=-1,
            last_contacted_at=aware_now,
            current_time=aware_now,
        )
    assert "rolling_contacts_7d" in str(exc_info.value)


def test_fatigue_suppression_boundary_precision() -> None:
    assert not is_fatigue_suppressed(3.99)
    assert not is_fatigue_suppressed(3.9999)
    assert is_fatigue_suppressed(4.0)
    assert is_fatigue_suppressed(4.0001)
    assert is_fatigue_suppressed(4.01)
