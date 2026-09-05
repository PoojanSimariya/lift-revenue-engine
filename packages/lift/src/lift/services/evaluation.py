"""Intervention Evaluation Service: generates and scores candidate intervention slates."""

from __future__ import annotations

from datetime import datetime, timezone

from lift.core.constants import (
    DEFAULT_BETA,
    DEFAULT_LAMBDA_FRICTION,
)
from lift.core.types import InterventionType
from lift.domain.models import (
    Customer,
    InterventionCandidate,
    RecoveryOpportunity,
)
from lift.economics.fatigue import calculate_contact_fatigue
from lift.economics.nirv import calculate_nirv


class InterventionEvaluationService:
    """Evaluates intervention candidates mathematically without mutating lifecycle.

    Does not authorize policies or execute actions.
    Computes recovery probabilities, organic counterfactuals, contact fatigue, direct costs,
    friction costs, model uncertainty penalties, and resulting NIRV in integer subunits.
    """

    def __init__(
        self,
        lambda_friction: float = DEFAULT_LAMBDA_FRICTION,
        beta: float = DEFAULT_BETA,
    ) -> None:
        self.lambda_friction = lambda_friction
        self.beta = beta

    def evaluate_single_candidate(
        self,
        opportunity: RecoveryOpportunity,
        customer: Customer,
        intervention_type: InterventionType,
        p_recovery: float,
        p_organic: float,
        confidence_score: float,
        parameters: dict[str, object] | None = None,
        eval_time: datetime | None = None,
    ) -> InterventionCandidate:
        """Score a single candidate intervention and return a populated InterventionCandidate."""
        now = eval_time or datetime.now(timezone.utc)

        # 1. Compute deterministic contact fatigue
        fatigue = calculate_contact_fatigue(
            intervention_type=intervention_type,
            rolling_contacts_7d=customer.rolling_contacts_7d,
            last_contacted_at=customer.last_contacted_at,
            current_time=now,
        )

        # 2. Compute NIRV breakdown
        breakdown = calculate_nirv(
            amount_at_risk_subunits=opportunity.amount_at_risk_subunits,
            p_recovery=p_recovery,
            p_organic=p_organic,
            intervention_type=intervention_type,
            contact_fatigue=fatigue,
            confidence_score=confidence_score,
            lambda_friction=self.lambda_friction,
            beta=self.beta,
        )

        return InterventionCandidate(
            opportunity_id=opportunity.id,
            intervention_type=intervention_type,
            parameters=parameters or {},
            p_recovery=p_recovery,
            p_organic=p_organic,
            direct_cost_subunits=breakdown.direct_cost_subunits,
            friction_cost_subunits=breakdown.friction_cost_subunits,
            risk_penalty_subunits=breakdown.risk_penalty_subunits,
            expected_net_value_subunits=breakdown.nirv_subunits,
            confidence_score=confidence_score,
            contact_fatigue=breakdown.contact_fatigue,
            generated_at=now,
        )

    def evaluate_all_candidates(
        self,
        opportunity: RecoveryOpportunity,
        customer: Customer,
        p_recovery_by_type: dict[InterventionType, float],
        p_organic: float,
        confidence_score: float,
        eval_time: datetime | None = None,
    ) -> list[InterventionCandidate]:
        """Evaluate the full slate of 6 supported intervention types."""
        candidates: list[InterventionCandidate] = []

        for itype in InterventionType:
            p_rec = p_recovery_by_type.get(itype, p_organic)
            candidate = self.evaluate_single_candidate(
                opportunity=opportunity,
                customer=customer,
                intervention_type=itype,
                p_recovery=p_rec,
                p_organic=p_organic,
                confidence_score=confidence_score,
                eval_time=eval_time,
            )
            candidates.append(candidate)

        return candidates
