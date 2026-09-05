"""Opportunity Lifecycle Service: pure application/domain lifecycle transitions."""

from __future__ import annotations

from uuid import UUID

from lift.core.types import (
    AttemptStatus,
    FailureCategory,
    OpportunityState,
    OrganicEstimationSource,
)
from lift.domain.models import PaymentAttempt, RecoveryOpportunity
from lift.domain.state_machine import OpportunityStateMachine, TransitionResult


class OpportunityLifecycleService:
    """Coordinates opportunity creation, attempt association, and synchronous state transitions.

    Does NOT calculate economics, authorize policies, or perform database persistence.
    """

    @staticmethod
    def create_opportunity_from_attempt(
        merchant_id: UUID,
        customer_id: UUID,
        order_id: str,
        initial_attempt: PaymentAttempt,
        failure_category: FailureCategory,
        organic_recovery_estimate: float,
        estimation_source: OrganicEstimationSource = OrganicEstimationSource.SEGMENT_PRIOR,
    ) -> tuple[RecoveryOpportunity, PaymentAttempt]:
        """Create a new RecoveryOpportunity associated with an initial failed attempt.

        Returns:
            tuple of (RecoveryOpportunity, linked PaymentAttempt)
        """
        opportunity = RecoveryOpportunity(
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id=order_id,
            initial_attempt_id=initial_attempt.id,
            latest_attempt_id=initial_attempt.id,
            amount_at_risk_subunits=initial_attempt.amount_subunits,
            currency=initial_attempt.currency,
            current_state=OpportunityState.OPEN,
            failure_category=failure_category,
            organic_recovery_estimate=organic_recovery_estimate,
            organic_estimation_source=estimation_source,
            failure_attempt_count=1,
            total_interventions_count=0,
            total_contacts_count=0,
        )

        initial_attempt.recovery_opportunity_id = opportunity.id
        return opportunity, initial_attempt

    @staticmethod
    def associate_additional_attempt(
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
    ) -> TransitionResult:
        """Associate a subsequent payment attempt with an existing opportunity."""
        attempt.recovery_opportunity_id = opportunity.id
        opportunity.latest_attempt_id = attempt.id

        # Update failure or capture via state machine
        if attempt.status == AttemptStatus.CAPTURED:
            return OpportunityStateMachine.handle_payment_captured(opportunity, "payment.captured")
        else:
            return OpportunityStateMachine.handle_payment_failed(opportunity, "payment.failed")

    # Synchronous Lifecycle State Transition Primitives
    @staticmethod
    def transition_to_in_evaluation(opportunity: RecoveryOpportunity) -> OpportunityState:
        """Synchronously transition OPEN -> IN_EVALUATION."""
        return OpportunityStateMachine.transition(
            opportunity, OpportunityState.IN_EVALUATION, reason="Opportunity claimed for evaluation"
        )

    @staticmethod
    def transition_to_action_scheduled(opportunity: RecoveryOpportunity) -> OpportunityState:
        """Synchronously transition IN_EVALUATION -> ACTION_SCHEDULED."""
        return OpportunityStateMachine.transition(
            opportunity, OpportunityState.ACTION_SCHEDULED, reason="Action authorized with schedule"
        )

    @staticmethod
    def transition_to_action_blocked(
        opportunity: RecoveryOpportunity, reason: str = ""
    ) -> OpportunityState:
        """Synchronously transition IN_EVALUATION -> ACTION_BLOCKED."""
        return OpportunityStateMachine.transition(
            opportunity,
            OpportunityState.ACTION_BLOCKED,
            reason=reason or "Action blocked by policy or negative NIRV",
        )

    @staticmethod
    def transition_to_escalated_human(
        opportunity: RecoveryOpportunity, reason: str = ""
    ) -> OpportunityState:
        """Synchronously transition IN_EVALUATION -> ESCALATED_HUMAN."""
        return OpportunityStateMachine.transition(
            opportunity,
            OpportunityState.ESCALATED_HUMAN,
            reason=reason or "Escalated for human operator review",
        )

    @staticmethod
    def transition_to_action_executing(opportunity: RecoveryOpportunity) -> OpportunityState:
        """Synchronously transition ACTION_SCHEDULED -> ACTION_EXECUTING."""
        return OpportunityStateMachine.transition(
            opportunity, OpportunityState.ACTION_EXECUTING, reason="Execution voucher claimed"
        )

    @staticmethod
    def transition_to_awaiting_settlement(opportunity: RecoveryOpportunity) -> OpportunityState:
        """Synchronously transition ACTION_EXECUTING -> AWAITING_SETTLEMENT."""
        return OpportunityStateMachine.transition(
            opportunity,
            OpportunityState.AWAITING_SETTLEMENT,
            reason="Intervention dispatched to gateway/channel",
        )

    @staticmethod
    def record_settlement_captured(
        opportunity: RecoveryOpportunity, event_name: str = "payment.captured"
    ) -> TransitionResult:
        """Monotonically transition to RECOVERED upon verified capture proof."""
        return OpportunityStateMachine.handle_payment_captured(opportunity, event_name)

    @staticmethod
    def transition_to_expired(opportunity: RecoveryOpportunity) -> OpportunityState:
        """Synchronously transition to terminal EXPIRED."""
        return OpportunityStateMachine.transition(
            opportunity, OpportunityState.EXPIRED, reason="Opportunity validity window elapsed"
        )

    @staticmethod
    def transition_to_terminated(
        opportunity: RecoveryOpportunity, reason: str = ""
    ) -> OpportunityState:
        """Synchronously transition to terminal TERMINATED."""
        return OpportunityStateMachine.transition(
            opportunity, OpportunityState.TERMINATED, reason=reason or "Opportunity terminated"
        )
