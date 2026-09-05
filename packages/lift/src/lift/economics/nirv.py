"""Deterministic Net Incremental Recovery Value (NIRV) calculation engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lift.core.constants import (
    DEFAULT_BETA,
    DEFAULT_LAMBDA_FRICTION,
    INT64_MAX,
    INT64_MIN,
)
from lift.core.errors import DataValidationError
from lift.core.types import InterventionType
from lift.economics.fatigue import NON_CONTACT_INTERVENTIONS, resolve_intervention_type

# Direct execution costs in integer currency subunits (paise for INR)
INTERVENTION_DIRECT_COSTS: Final[dict[InterventionType, int]] = {
    InterventionType.NO_ACTION: 0,
    InterventionType.INTERNAL_RETRY_SCHEDULE: 0,
    InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 10,  # 10 paise (0.10 INR)
    InterventionType.DIRECT_PAYMENT_LINK_SMS: 25,  # 25 paise (0.25 INR)
    InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 80,  # 80 paise (0.80 INR)
    InterventionType.CUSTOM_WEBHOOK_OUTREACH: 5,  # 5 paise (0.05 INR)
}


@dataclass(frozen=True, slots=True)
class NirvBreakdown:
    """Detailed mathematical breakdown of NIRV components in integer subunits."""

    expected_incremental_recovery_subunits: int
    direct_cost_subunits: int
    friction_cost_subunits: int
    risk_penalty_subunits: int
    nirv_subunits: int
    amount_at_risk_subunits: int
    p_recovery: float
    p_organic: float
    contact_fatigue: float
    confidence_score: float
    uncertainty: float

    @property
    def is_positive(self) -> bool:
        """True if NIRV produces a strictly positive expected net incremental value."""
        return self.nirv_subunits > 0


def get_direct_cost_subunits(intervention_type: InterventionType | str) -> int:
    """Resolve direct provider dispatch cost in paise for an intervention type.

    Raises:
        DataValidationError: If the intervention type is not recognized in approved taxonomy.
    """
    resolved = resolve_intervention_type(intervention_type)
    return INTERVENTION_DIRECT_COSTS[resolved]


def calculate_nirv(
    amount_at_risk_subunits: int,
    p_recovery: float,
    p_organic: float,
    intervention_type: InterventionType | str,
    contact_fatigue: float,
    confidence_score: float,
    direct_cost_override_subunits: int | None = None,
    lambda_friction: float = DEFAULT_LAMBDA_FRICTION,
    beta: float = DEFAULT_BETA,
) -> NirvBreakdown:
    """Compute the Net Incremental Recovery Value (NIRV) strictly according to approved formulation.

    Formula:
        NIRV(a, i) = E[Delta_RecoveredValue] - DirectCost - FrictionCost - RiskPenalty
        where:
            E[Delta_RecoveredValue] = (p_recovery - p_organic) * AmountAtRisk
            FrictionCost = lambda_friction * AmountAtRisk * ContactFatigue (0 for non-contact)
            RiskPenalty = beta * Uncertainty * AmountAtRisk
            Uncertainty = 1.0 - confidence_score

    All monetary components are deterministically rounded to integer currency subunits (paise).

    Args:
        amount_at_risk_subunits: Gross order value in integer subunits (paise),
            bounded by INT64_MAX.
        p_recovery: Estimated recovery probability under this candidate action in [0.0, 1.0].
        p_organic: Counterfactual baseline organic recovery probability in [0.0, 1.0].
        intervention_type: Candidate action being evaluated (must be approved taxonomy).
        contact_fatigue: Pre-computed contact fatigue value.
        confidence_score: Calibrated classification confidence in [0.50, 1.0].
        direct_cost_override_subunits: Optional explicit direct cost.
        lambda_friction: Friction cost coefficient (default 0.05).
        beta: Risk penalty weight (default 0.10).

    Returns:
        NirvBreakdown: Complete structured breakdown of economic terms.

    Raises:
        DataValidationError: If inputs or outputs violate numeric or integer bounds.
    """
    if isinstance(amount_at_risk_subunits, bool) or not isinstance(amount_at_risk_subunits, int):
        raise DataValidationError(
            "amount_at_risk_subunits", amount_at_risk_subunits, "Must be an integer."
        )
    if amount_at_risk_subunits < 0 or amount_at_risk_subunits > INT64_MAX:
        raise DataValidationError(
            "amount_at_risk_subunits",
            amount_at_risk_subunits,
            f"Must be between 0 and INT64_MAX ({INT64_MAX}).",
        )
    if not (0.0 <= p_recovery <= 1.0):
        raise DataValidationError("p_recovery", p_recovery, "Must be in [0.0, 1.0].")
    if not (0.0 <= p_organic <= 1.0):
        raise DataValidationError("p_organic", p_organic, "Must be in [0.0, 1.0].")
    if not (0.50 <= confidence_score <= 1.0):
        raise DataValidationError(
            "confidence_score",
            confidence_score,
            "Must be in [0.50, 1.0] (uncertainty in [0.0, 0.50]).",
        )
    if contact_fatigue < 0.0:
        raise DataValidationError(
            "contact_fatigue", contact_fatigue, "Contact fatigue cannot be negative."
        )

    resolved_type = resolve_intervention_type(intervention_type)
    is_non_contact = resolved_type in NON_CONTACT_INTERVENTIONS

    # 1. Expected Incremental Recovery
    delta_p = p_recovery - p_organic
    expected_incremental_float = delta_p * amount_at_risk_subunits
    expected_incremental_subunits = int(round(expected_incremental_float))

    # 2. Direct Execution Cost
    if is_non_contact:
        direct_cost_subunits = 0
    elif direct_cost_override_subunits is not None:
        if direct_cost_override_subunits < 0:
            raise DataValidationError(
                "direct_cost_override_subunits",
                direct_cost_override_subunits,
                "Direct cost cannot be negative.",
            )
        direct_cost_subunits = direct_cost_override_subunits
    else:
        direct_cost_subunits = get_direct_cost_subunits(resolved_type)

    # 3. Friction Cost
    if is_non_contact:
        friction_cost_subunits = 0
    else:
        friction_float = lambda_friction * amount_at_risk_subunits * contact_fatigue
        friction_cost_subunits = int(round(friction_float))

    # 4. Risk Penalty
    uncertainty = round(1.0 - confidence_score, 4)
    risk_float = beta * uncertainty * amount_at_risk_subunits
    risk_penalty_subunits = int(round(risk_float))

    # 5. NIRV Total
    nirv_subunits = (
        expected_incremental_subunits
        - direct_cost_subunits
        - friction_cost_subunits
        - risk_penalty_subunits
    )

    if not (INT64_MIN <= nirv_subunits <= INT64_MAX):
        raise DataValidationError(
            "nirv_subunits",
            nirv_subunits,
            f"Calculated NIRV exceeds 64-bit signed integer limits [{INT64_MIN}, {INT64_MAX}].",
        )

    return NirvBreakdown(
        expected_incremental_recovery_subunits=expected_incremental_subunits,
        direct_cost_subunits=direct_cost_subunits,
        friction_cost_subunits=friction_cost_subunits,
        risk_penalty_subunits=risk_penalty_subunits,
        nirv_subunits=nirv_subunits,
        amount_at_risk_subunits=amount_at_risk_subunits,
        p_recovery=p_recovery,
        p_organic=p_organic,
        contact_fatigue=contact_fatigue if not is_non_contact else 0.0,
        confidence_score=confidence_score,
        uncertainty=uncertainty,
    )
