"""Deterministic Policy Gate service for guardrail enforcement and candidate authorization."""

from __future__ import annotations

from datetime import datetime, timezone

from lift.core.constants import FATIGUE_SUPPRESSION_THRESHOLD
from lift.core.types import DecisionType, InterventionType
from lift.domain.models import (
    Customer,
    InterventionCandidate,
    Merchant,
    RecoveryDecision,
    RecoveryOpportunity,
)
from lift.economics.fatigue import (
    NON_CONTACT_INTERVENTIONS,
    is_fatigue_suppressed,
    resolve_intervention_type,
)
from lift.policies.rules import ContactCapConfig, QuietHoursConfig


class PolicyGateService:
    """Evaluates candidate interventions against deterministic merchant guardrails.

    Enforces:
    - Quiet hours in the merchant's configured timezone (stdlib zoneinfo).
    - Customer rolling 7-day contact limit (N >= 3 blocks direct outreach).
    - Hard contact fatigue threshold (fatigue >= 4.0 blocks direct outreach).
    - Positive NIRV requirement.
    """

    def __init__(
        self,
        quiet_hours_config: QuietHoursConfig | None = None,
        contact_cap_config: ContactCapConfig | None = None,
    ) -> None:
        self.quiet_hours_config = quiet_hours_config or QuietHoursConfig()
        self.contact_cap_config = contact_cap_config or ContactCapConfig()

    def evaluate_candidate(
        self,
        candidate: InterventionCandidate,
        opportunity: RecoveryOpportunity,
        merchant: Merchant,
        customer: Customer,
        eval_time: datetime | None = None,
    ) -> RecoveryDecision:
        """Evaluate an individual intervention candidate against deterministic policies."""
        now = eval_time or datetime.now(timezone.utc)
        resolved_type = resolve_intervention_type(candidate.intervention_type)
        is_contact = resolved_type not in NON_CONTACT_INTERVENTIONS
        type_str = resolved_type.value

        policy_details: dict[str, object] = {
            "intervention_type": type_str,
            "is_contact_action": is_contact,
            "expected_net_value_subunits": candidate.expected_net_value_subunits,
            "eval_time_utc": now.isoformat(),
            "merchant_timezone": merchant.timezone,
        }

        # 1. Economic Viability Check (NIRV must be positive for active interventions)
        if type_str != InterventionType.NO_ACTION.value:
            if candidate.expected_net_value_subunits <= 0:
                policy_details["reason"] = "Candidate expected net value is non-positive."
                return RecoveryDecision(
                    opportunity_id=opportunity.id,
                    selected_candidate_id=candidate.id,
                    decision_type=DecisionType.BLOCKED,
                    policy_evaluation_details=policy_details,
                    blocked_reason_code="BLOCKED_NON_POSITIVE_NIRV",
                    explanation=(
                        f"Intervention {type_str} blocked: "
                        f"NIRV ({candidate.expected_net_value_subunits} paise) is non-positive."
                    ),
                )

        # Non-contact actions (NO_ACTION, INTERNAL_RETRY_SCHEDULE) bypass customer-facing guardrails
        if not is_contact:
            if type_str == InterventionType.NO_ACTION.value:
                return RecoveryDecision(
                    opportunity_id=opportunity.id,
                    selected_candidate_id=candidate.id,
                    decision_type=DecisionType.NO_ACTION,
                    policy_evaluation_details=policy_details,
                    blocked_reason_code=None,
                    explanation="Passive wait / no action selected.",
                )
            else:
                return RecoveryDecision(
                    opportunity_id=opportunity.id,
                    selected_candidate_id=candidate.id,
                    decision_type=DecisionType.AUTHORIZED,
                    policy_evaluation_details=policy_details,
                    blocked_reason_code=None,
                    explanation=f"Internal non-contact intervention {type_str} authorized.",
                )

        # 2. Customer Outreach Guardrails
        # A. Quiet Hours Check in merchant timezone
        is_quiet = self.quiet_hours_config.is_quiet_hour(now, merchant.timezone)
        policy_details["is_quiet_hour"] = is_quiet
        if is_quiet:
            policy_details["reason"] = f"Quiet hours active in {merchant.timezone}."
            return RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=candidate.id,
                decision_type=DecisionType.BLOCKED,
                policy_evaluation_details=policy_details,
                blocked_reason_code="BLOCKED_QUIET_HOURS",
                explanation=(
                    f"Customer outreach blocked during quiet hours in {merchant.timezone}."
                ),
            )

        # B. Rolling 7-day Contact Limit Check
        policy_details["rolling_contacts_7d"] = customer.rolling_contacts_7d
        if self.contact_cap_config.is_limit_exceeded(customer.rolling_contacts_7d):
            policy_details["reason"] = (
                f"Customer rolling 7-day contacts ({customer.rolling_contacts_7d}) exceeds limit."
            )
            return RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=candidate.id,
                decision_type=DecisionType.BLOCKED,
                policy_evaluation_details=policy_details,
                blocked_reason_code="BLOCKED_CONTACT_LIMIT",
                explanation=(
                    f"Customer reached contact limit ({customer.rolling_contacts_7d} "
                    f">= {self.contact_cap_config.max_contacts_7d})."
                ),
            )

        # C. Contact Fatigue Hard Cutoff Check
        policy_details["contact_fatigue"] = candidate.contact_fatigue
        if is_fatigue_suppressed(candidate.contact_fatigue):
            policy_details["reason"] = (
                f"Contact fatigue ({candidate.contact_fatigue}) meets or exceeds "
                f"suppression threshold ({FATIGUE_SUPPRESSION_THRESHOLD})."
            )
            return RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=candidate.id,
                decision_type=DecisionType.BLOCKED,
                policy_evaluation_details=policy_details,
                blocked_reason_code="BLOCKED_CONTACT_FATIGUE",
                explanation=(
                    f"Customer outreach blocked: contact fatigue ({candidate.contact_fatigue}) "
                    f"meets or exceeds suppression threshold ({FATIGUE_SUPPRESSION_THRESHOLD})."
                ),
            )

        # If all checks pass:
        return RecoveryDecision(
            opportunity_id=opportunity.id,
            selected_candidate_id=candidate.id,
            decision_type=DecisionType.AUTHORIZED,
            policy_evaluation_details=policy_details,
            blocked_reason_code=None,
            explanation=(
                f"Intervention {type_str} authorized with positive NIRV "
                f"({candidate.expected_net_value_subunits} paise)."
            ),
        )

    def select_best_candidate(
        self,
        candidates: list[InterventionCandidate],
        opportunity: RecoveryOpportunity,
        merchant: Merchant,
        customer: Customer,
        eval_time: datetime | None = None,
    ) -> RecoveryDecision:
        """Evaluate a candidate slate and select the highest-NIRV authorized candidate."""
        if not candidates:
            return RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=None,
                decision_type=DecisionType.NO_ACTION,
                policy_evaluation_details={"reason": "Candidate slate is empty."},
                blocked_reason_code="NO_CANDIDATES",
                explanation="No intervention candidates provided for evaluation.",
            )

        authorized_decisions: list[tuple[InterventionCandidate, RecoveryDecision]] = []
        blocked_decisions: list[tuple[InterventionCandidate, RecoveryDecision]] = []

        for candidate in candidates:
            decision = self.evaluate_candidate(
                candidate, opportunity, merchant, customer, eval_time
            )
            if decision.decision_type == DecisionType.AUTHORIZED:
                authorized_decisions.append((candidate, decision))
            else:
                blocked_decisions.append((candidate, decision))

        if authorized_decisions:
            # Sort by highest expected net value (NIRV)
            authorized_decisions.sort(
                key=lambda item: item[0].expected_net_value_subunits,
                reverse=True,
            )
            best_candidate, best_decision = authorized_decisions[0]
            return best_decision

        # If no active candidate was authorized, return NO_ACTION or first blocked reason
        no_action_candidate = next(
            (c for c in candidates if c.intervention_type == InterventionType.NO_ACTION),
            None,
        )
        if no_action_candidate:
            return RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=no_action_candidate.id,
                decision_type=DecisionType.NO_ACTION,
                policy_evaluation_details={
                    "reason": "All active candidates blocked; falling back to NO_ACTION."
                },
                blocked_reason_code=None,
                explanation=(
                    "All candidate interventions blocked by policy or negative NIRV; "
                    "selecting passive wait."
                ),
            )

        fallback_decision = (
            blocked_decisions[0][1]
            if blocked_decisions
            else RecoveryDecision(
                opportunity_id=opportunity.id,
                selected_candidate_id=None,
                decision_type=DecisionType.BLOCKED,
                policy_evaluation_details={"reason": "All candidates blocked."},
                blocked_reason_code="ALL_CANDIDATES_BLOCKED",
                explanation="No candidate could be authorized under active policies.",
            )
        )
        return fallback_decision
