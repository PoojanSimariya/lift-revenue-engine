"""Deterministic 11-state opportunity lifecycle state machine with monotonic terminal sinks."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from lift.core.errors import InvalidStateTransitionError, TerminalStateMutationError
from lift.core.types import OpportunityState
from lift.domain.models import RecoveryOpportunity

TERMINAL_STATES: Final[frozenset[OpportunityState]] = frozenset(
    {
        OpportunityState.RECOVERED,
        OpportunityState.EXPIRED,
        OpportunityState.TERMINATED,
    }
)

VALID_TRANSITIONS: Final[dict[OpportunityState, frozenset[OpportunityState]]] = {
    OpportunityState.OPEN: frozenset(
        {
            OpportunityState.IN_EVALUATION,
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.IN_EVALUATION: frozenset(
        {
            OpportunityState.ACTION_SCHEDULED,
            OpportunityState.ACTION_BLOCKED,
            OpportunityState.ESCALATED_HUMAN,
            OpportunityState.OPEN,  # Timeout / evaluation lease expiry
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.ACTION_SCHEDULED: frozenset(
        {
            OpportunityState.ACTION_EXECUTING,
            OpportunityState.ACTION_BLOCKED,
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.ACTION_EXECUTING: frozenset(
        {
            OpportunityState.AWAITING_SETTLEMENT,
            OpportunityState.RECONCILIATION_REQUIRED,
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.AWAITING_SETTLEMENT: frozenset(
        {
            OpportunityState.RECOVERED,
            OpportunityState.OPEN,  # Payment link expired, retry budget remains
            OpportunityState.EXPIRED,  # Payment link expired, retry budget exhausted
            OpportunityState.TERMINATED,
        }
    ),
    OpportunityState.RECONCILIATION_REQUIRED: frozenset(
        {
            OpportunityState.AWAITING_SETTLEMENT,  # Link verified to exist
            OpportunityState.OPEN,  # Call verified to have never executed
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.ACTION_BLOCKED: frozenset(
        {
            OpportunityState.OPEN,  # Window elapses or new attempt ingested
            OpportunityState.RECOVERED,
            OpportunityState.TERMINATED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.ESCALATED_HUMAN: frozenset(
        {
            OpportunityState.ACTION_SCHEDULED,  # Operator approved
            OpportunityState.TERMINATED,  # Operator closed case
            OpportunityState.RECOVERED,
            OpportunityState.EXPIRED,
        }
    ),
    OpportunityState.RECOVERED: frozenset(),  # Monotonic terminal sink
    OpportunityState.EXPIRED: frozenset(),  # Terminal sink
    OpportunityState.TERMINATED: frozenset(),  # Terminal sink
}


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a state transition or event handling attempt."""

    previous_state: OpportunityState
    current_state: OpportunityState
    transitioned: bool
    event_name: str
    suppressed: bool = False
    message: str = ""


class OpportunityStateMachine:
    """Synchronous, deterministic state machine enforcing lifecycle transition invariants."""

    @staticmethod
    def is_terminal(state: OpportunityState) -> bool:
        """True if state is one of the immutable terminal states."""
        return state in TERMINAL_STATES

    @classmethod
    def can_transition(cls, from_state: OpportunityState, to_state: OpportunityState) -> bool:
        """Check if transition from from_state to to_state is permissible."""
        return to_state in VALID_TRANSITIONS.get(from_state, frozenset())

    @classmethod
    def transition(
        cls,
        opportunity: RecoveryOpportunity,
        to_state: OpportunityState,
        reason: str = "",
    ) -> OpportunityState:
        """Perform a synchronous transition on an opportunity.

        Raises:
            TerminalStateMutationError: If opportunity is in a terminal sink.
            InvalidStateTransitionError: If transition is not legally permitted.
        """
        from_state = opportunity.current_state

        if cls.is_terminal(from_state):
            raise TerminalStateMutationError(from_state.value, f"transition to {to_state.value}")

        if not cls.can_transition(from_state, to_state):
            raise InvalidStateTransitionError(from_state.value, to_state.value, reason)

        now = datetime.now(timezone.utc)
        opportunity.current_state = to_state
        opportunity.version += 1

        if to_state == OpportunityState.IN_EVALUATION:
            opportunity.last_evaluated_at = now
        elif to_state == OpportunityState.ACTION_EXECUTING:
            opportunity.execution_claimed_at = now
        elif cls.is_terminal(to_state):
            opportunity.closed_at = now

        return to_state

    @classmethod
    def handle_payment_captured(
        cls,
        opportunity: RecoveryOpportunity,
        event_name: str = "payment.captured",
    ) -> TransitionResult:
        """Handle verified capture settlement proof.

        Transitions any non-terminal opportunity to RECOVERED (monotonic terminal sink).
        If already RECOVERED, safely no-ops without error.
        """
        previous_state = opportunity.current_state

        if previous_state == OpportunityState.RECOVERED:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name=event_name,
                suppressed=True,
                message="Opportunity already RECOVERED. Duplicate capture acknowledged.",
            )

        if cls.is_terminal(previous_state):
            raise TerminalStateMutationError(
                previous_state.value, f"handle capture proof for {event_name}"
            )

        cls.transition(
            opportunity, OpportunityState.RECOVERED, reason=f"Capture verified via {event_name}"
        )
        return TransitionResult(
            previous_state=previous_state,
            current_state=OpportunityState.RECOVERED,
            transitioned=True,
            event_name=event_name,
            suppressed=False,
            message="Opportunity monotonically transitioned to RECOVERED.",
        )

    @classmethod
    def handle_payment_failed(
        cls,
        opportunity: RecoveryOpportunity,
        event_name: str = "payment.failed",
        increment_attempt_count: bool = True,
    ) -> TransitionResult:
        """Handle payment failure event.

        CRITICAL INVARIANT:
        If opportunity is already RECOVERED, late failure events must NOT mutate state.
        Produces STALE_FAILURE_SUPPRESSED result without raising an error.
        Failure count increments ONLY when increment_attempt_count is True (new attempt).
        """
        previous_state = opportunity.current_state

        if previous_state == OpportunityState.RECOVERED:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="STALE_FAILURE_SUPPRESSED",
                suppressed=True,
                message="Late failure event suppressed for permanently RECOVERED opportunity.",
            )

        if cls.is_terminal(previous_state):
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="TERMINAL_FAILURE_IGNORED",
                suppressed=True,
                message=f"Failure ignored on terminal state {previous_state.value}.",
            )

        if increment_attempt_count:
            opportunity.failure_attempt_count += 1

        if previous_state == OpportunityState.ACTION_BLOCKED:
            cls.transition(
                opportunity,
                OpportunityState.OPEN,
                reason="New payment failure ingested while blocked",
            )
            return TransitionResult(
                previous_state=previous_state,
                current_state=OpportunityState.OPEN,
                transitioned=True,
                event_name=event_name,
                suppressed=False,
                message="Opportunity unblocked to OPEN on new failure attempt.",
            )

        return TransitionResult(
            previous_state=previous_state,
            current_state=previous_state,
            transitioned=False,
            event_name=event_name,
            suppressed=False,
            message="Payment failure recorded.",
        )

    @classmethod
    def handle_payment_authorized(
        cls,
        opportunity: RecoveryOpportunity,
        event_name: str = "payment.authorized",
    ) -> TransitionResult:
        """Handle payment authorization event.

        Preserves or sets AWAITING_SETTLEMENT. Does NOT mark RECOVERED on authorization alone.
        Suppresses when already in RECOVERED terminal sink.
        """
        previous_state = opportunity.current_state

        if previous_state == OpportunityState.RECOVERED:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="STALE_AUTH_SUPPRESSED",
                suppressed=True,
                message="Payment authorization suppressed on RECOVERED opportunity.",
            )

        if cls.is_terminal(previous_state):
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="TERMINAL_EVENT_IGNORED",
                suppressed=True,
                message=f"Authorization ignored on terminal state {previous_state.value}.",
            )

        if previous_state == OpportunityState.AWAITING_SETTLEMENT:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name=event_name,
                suppressed=False,
                message="Payment authorized while already in AWAITING_SETTLEMENT.",
            )

        if not cls.can_transition(previous_state, OpportunityState.AWAITING_SETTLEMENT):
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="UNSUPPORTED_AUTH_TRANSITION_SUPPRESSED",
                suppressed=True,
                message=f"Cannot transition to AWAITING_SETTLEMENT from {previous_state.value}.",
            )

        cls.transition(
            opportunity,
            OpportunityState.AWAITING_SETTLEMENT,
            reason=f"Payment authorized via {event_name}",
        )
        return TransitionResult(
            previous_state=previous_state,
            current_state=OpportunityState.AWAITING_SETTLEMENT,
            transitioned=True,
            event_name=event_name,
            suppressed=False,
            message="Opportunity transitioned to AWAITING_SETTLEMENT on authorization.",
        )

    @classmethod
    def handle_payment_link_partially_paid(
        cls,
        opportunity: RecoveryOpportunity,
        event_name: str = "payment_link.partially_paid",
    ) -> TransitionResult:
        """Handle partial payment on payment link.

        Records evidence but does NOT transition to RECOVERED (amount at risk not fully settled).
        Suppresses when already permanently RECOVERED.
        """
        previous_state = opportunity.current_state

        if previous_state == OpportunityState.RECOVERED:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="STALE_PARTIAL_PAID_SUPPRESSED",
                suppressed=True,
                message="Partial payment event suppressed on permanently RECOVERED opportunity.",
            )

        if cls.is_terminal(previous_state):
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="TERMINAL_EVENT_IGNORED",
                suppressed=True,
                message=f"Partial payment ignored on terminal state {previous_state.value}.",
            )

        return TransitionResult(
            previous_state=previous_state,
            current_state=previous_state,
            transitioned=False,
            event_name=event_name,
            suppressed=False,
            message="Partial payment recorded; opportunity remains non-terminal.",
        )

    @classmethod
    def handle_payment_link_expired(
        cls,
        opportunity: RecoveryOpportunity,
        retry_budget_remaining: bool,
    ) -> TransitionResult:
        """Handle payment link expiration.

        If already RECOVERED, event is suppressed.
        If in AWAITING_SETTLEMENT:
          - transitions to OPEN if retry_budget_remaining is True
          - transitions to EXPIRED if retry_budget_remaining is False
        """
        previous_state = opportunity.current_state

        if previous_state == OpportunityState.RECOVERED:
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="STALE_FAILURE_SUPPRESSED",
                suppressed=True,
                message="Link expiration suppressed on RECOVERED opportunity.",
            )

        if cls.is_terminal(previous_state):
            return TransitionResult(
                previous_state=previous_state,
                current_state=previous_state,
                transitioned=False,
                event_name="TERMINAL_EVENT_IGNORED",
                suppressed=True,
                message=f"Link expiration ignored on terminal state {previous_state.value}.",
            )

        if previous_state == OpportunityState.AWAITING_SETTLEMENT:
            to_state = OpportunityState.OPEN if retry_budget_remaining else OpportunityState.EXPIRED
            cls.transition(
                opportunity,
                to_state,
                reason="Link expired with retry budget"
                if retry_budget_remaining
                else "Link expired, budget exhausted",
            )
            return TransitionResult(
                previous_state=previous_state,
                current_state=to_state,
                transitioned=True,
                event_name="payment_link.expired",
                suppressed=False,
                message=f"Opportunity transitioned to {to_state.value} on link expiration.",
            )

        return TransitionResult(
            previous_state=previous_state,
            current_state=previous_state,
            transitioned=False,
            event_name="payment_link.expired",
            suppressed=False,
            message=f"No transition for link expiration from state {previous_state.value}.",
        )
