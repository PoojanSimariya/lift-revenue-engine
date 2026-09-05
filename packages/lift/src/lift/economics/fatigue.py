"""Deterministic closed-form Contact Fatigue calculation with exponential recency decay."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Final

from lift.core.constants import (
    FATIGUE_HALF_LIFE_HOURS,
    FATIGUE_SUPPRESSION_THRESHOLD,
)
from lift.core.errors import DataValidationError
from lift.core.types import InterventionType

# Interventions that do not contact the customer directly
NON_CONTACT_INTERVENTIONS: Final[frozenset[InterventionType]] = frozenset(
    {
        InterventionType.NO_ACTION,
        InterventionType.INTERNAL_RETRY_SCHEDULE,
    }
)

# Intrusion weights for customer contact channels
CONTACT_INTERVENTION_WEIGHTS: Final[dict[InterventionType, float]] = {
    InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 1.5,
    InterventionType.DIRECT_PAYMENT_LINK_SMS: 1.0,
    InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.4,
    InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.8,
}


def resolve_intervention_type(intervention_type: InterventionType | str) -> InterventionType:
    """Resolve an intervention type enum or string to a validated InterventionType.

    Raises:
        DataValidationError: If the intervention type is not in the approved taxonomy.
    """
    if isinstance(intervention_type, InterventionType):
        return intervention_type
    try:
        return InterventionType(str(intervention_type).strip())
    except ValueError:
        raise DataValidationError(
            "intervention_type",
            intervention_type,
            f"Unknown intervention type '{intervention_type}'. "
            f"Must be one of {[t.value for t in InterventionType]}.",
        )


def get_channel_weight(intervention_type: InterventionType | str) -> float:
    """Resolve the channel intrusion weight w(a) for a given intervention type.

    Raises:
        DataValidationError: If the intervention type is not recognized in the approved taxonomy.
    """
    resolved = resolve_intervention_type(intervention_type)
    if resolved in NON_CONTACT_INTERVENTIONS:
        return 0.0
    return CONTACT_INTERVENTION_WEIGHTS[resolved]


def calculate_contact_fatigue(
    intervention_type: InterventionType | str,
    rolling_contacts_7d: int,
    last_contacted_at: datetime | None,
    current_time: datetime | None = None,
    channel_weight_override: float | None = None,
) -> float:
    """Calculate the deterministic ContactFatigue metric according to approved architecture.

    Formula:
        ContactFatigue = w(a) * (1.0 + R(t, t_last) + 0.5 * N)
        where R(t, t_last) = exp(-delta_hours / 48.0)
        If non-contact action (NO_ACTION, INTERNAL_RETRY_SCHEDULE): ContactFatigue = 0.0
        If first contact (last_contacted_at is None): R = 0.0

    Args:
        intervention_type: Candidate action being evaluated (must be approved taxonomy).
        rolling_contacts_7d: Non-negative count of customer contacts in past 7 days.
        last_contacted_at: Timezone-aware timestamp of most recent customer contact (nullable).
        current_time: Timezone-aware reference evaluation time (defaults to UTC now).
        channel_weight_override: Optional explicit channel weight w(a).

    Returns:
        float: Computed fatigue value rounded to 4 decimal places.

    Raises:
        DataValidationError: If datetimes are naive, count is negative, or intervention is unknown.
    """
    resolved_type = resolve_intervention_type(intervention_type)

    if resolved_type in NON_CONTACT_INTERVENTIONS:
        return 0.0

    if rolling_contacts_7d < 0:
        raise DataValidationError(
            "rolling_contacts_7d",
            rolling_contacts_7d,
            "Rolling contacts count cannot be negative.",
        )

    # Enforce timezone-aware datetimes (never silently assume naive is UTC)
    if current_time is not None and (
        current_time.tzinfo is None or current_time.tzinfo.utcoffset(current_time) is None
    ):
        raise DataValidationError(
            "current_time",
            current_time,
            "Evaluation datetime must be timezone-aware.",
        )

    if last_contacted_at is not None and (
        last_contacted_at.tzinfo is None
        or last_contacted_at.tzinfo.utcoffset(last_contacted_at) is None
    ):
        raise DataValidationError(
            "last_contacted_at",
            last_contacted_at,
            "Last contact datetime must be timezone-aware.",
        )

    w = (
        channel_weight_override
        if channel_weight_override is not None
        else get_channel_weight(resolved_type)
    )
    n = rolling_contacts_7d

    if last_contacted_at is None:
        recency_factor = 0.0
    else:
        eval_time = current_time or datetime.now(timezone.utc)
        delta_seconds = (eval_time - last_contacted_at).total_seconds()
        delta_hours = max(0.0, delta_seconds / 3600.0)
        recency_factor = math.exp(-delta_hours / FATIGUE_HALF_LIFE_HOURS)

    fatigue = w * (1.0 + recency_factor + 0.5 * n)
    return round(fatigue, 4)


def is_fatigue_suppressed(fatigue: float) -> bool:
    """True if contact fatigue meets or exceeds the hard suppression threshold (>= 4.0)."""
    return fatigue >= FATIGUE_SUPPRESSION_THRESHOLD
