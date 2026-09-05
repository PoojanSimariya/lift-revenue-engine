"""Unit tests for OpportunityStateMachine and lifecycle transition invariants."""

import pytest
from lift.core.errors import InvalidStateTransitionError, TerminalStateMutationError
from lift.core.types import OpportunityState
from lift.domain.models import RecoveryOpportunity
from lift.domain.state_machine import OpportunityStateMachine


def test_state_machine_valid_full_lifecycle(sample_opportunity: RecoveryOpportunity) -> None:
    opp = sample_opportunity
    assert opp.current_state == OpportunityState.OPEN

    # OPEN -> IN_EVALUATION
    OpportunityStateMachine.transition(opp, OpportunityState.IN_EVALUATION)
    assert opp.current_state == OpportunityState.IN_EVALUATION
    assert opp.last_evaluated_at is not None

    # IN_EVALUATION -> ACTION_SCHEDULED
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_SCHEDULED)
    assert opp.current_state == OpportunityState.ACTION_SCHEDULED

    # ACTION_SCHEDULED -> ACTION_EXECUTING
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_EXECUTING)
    assert opp.current_state == OpportunityState.ACTION_EXECUTING
    assert opp.execution_claimed_at is not None

    # ACTION_EXECUTING -> AWAITING_SETTLEMENT
    OpportunityStateMachine.transition(opp, OpportunityState.AWAITING_SETTLEMENT)
    assert opp.current_state == OpportunityState.AWAITING_SETTLEMENT

    # AWAITING_SETTLEMENT -> RECOVERED (Terminal)
    res = OpportunityStateMachine.handle_payment_captured(opp)
    assert res.transitioned
    assert opp.current_state == OpportunityState.RECOVERED
    assert opp.closed_at is not None


def test_state_machine_monotonic_terminal_sink(sample_opportunity: RecoveryOpportunity) -> None:
    opp = sample_opportunity

    # Move to RECOVERED directly via capture
    OpportunityStateMachine.handle_payment_captured(opp)
    assert opp.current_state == OpportunityState.RECOVERED

    # 1. Attempting transition to any state raises TerminalStateMutationError
    for state in OpportunityState:
        with pytest.raises(TerminalStateMutationError):
            OpportunityStateMachine.transition(opp, state)

    # 2. Subsequent capture events safely drop/suppress without error
    res_cap = OpportunityStateMachine.handle_payment_captured(opp)
    assert not res_cap.transitioned
    assert res_cap.suppressed
    assert opp.current_state == OpportunityState.RECOVERED

    # 3. Subsequent late payment failure webhooks produce STALE_FAILURE_SUPPRESSED
    res_fail = OpportunityStateMachine.handle_payment_failed(opp)
    assert not res_fail.transitioned
    assert res_fail.suppressed
    assert res_fail.event_name == "STALE_FAILURE_SUPPRESSED"
    assert opp.current_state == OpportunityState.RECOVERED

    # 4. Subsequent link expiration events produce STALE_FAILURE_SUPPRESSED
    res_exp = OpportunityStateMachine.handle_payment_link_expired(opp, retry_budget_remaining=True)
    assert not res_exp.transitioned
    assert res_exp.suppressed
    assert res_exp.event_name == "STALE_FAILURE_SUPPRESSED"
    assert opp.current_state == OpportunityState.RECOVERED


def test_state_machine_invalid_transitions(sample_opportunity: RecoveryOpportunity) -> None:
    opp = sample_opportunity
    assert opp.current_state == OpportunityState.OPEN

    # Illegal direct bypass from OPEN to ACTION_EXECUTING
    # (normal path requires IN_EVALUATION -> ACTION_SCHEDULED)
    with pytest.raises(InvalidStateTransitionError):
        OpportunityStateMachine.transition(opp, OpportunityState.ACTION_EXECUTING)

    # Illegal jump from OPEN to AWAITING_SETTLEMENT
    with pytest.raises(InvalidStateTransitionError):
        OpportunityStateMachine.transition(opp, OpportunityState.AWAITING_SETTLEMENT)

    # Illegal jump from OPEN to RECONCILIATION_REQUIRED
    with pytest.raises(InvalidStateTransitionError):
        OpportunityStateMachine.transition(opp, OpportunityState.RECONCILIATION_REQUIRED)


def test_state_machine_reconciliation_paths(sample_opportunity: RecoveryOpportunity) -> None:
    opp = sample_opportunity

    # Advance through canonical path: OPEN -> IN_EVALUATION -> ACTION_SCHEDULED -> ACTION_EXECUTING
    OpportunityStateMachine.transition(opp, OpportunityState.IN_EVALUATION)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_SCHEDULED)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_EXECUTING)

    # Move to RECONCILIATION_REQUIRED
    OpportunityStateMachine.transition(opp, OpportunityState.RECONCILIATION_REQUIRED)
    assert opp.current_state == OpportunityState.RECONCILIATION_REQUIRED

    # Branch A: Gateway confirms link dispatched -> AWAITING_SETTLEMENT
    OpportunityStateMachine.transition(opp, OpportunityState.AWAITING_SETTLEMENT)
    assert opp.current_state == OpportunityState.AWAITING_SETTLEMENT


def test_state_machine_blocked_and_unblock_on_failure(
    sample_opportunity: RecoveryOpportunity,
) -> None:
    opp = sample_opportunity
    OpportunityStateMachine.transition(opp, OpportunityState.IN_EVALUATION)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_BLOCKED)
    assert opp.current_state == OpportunityState.ACTION_BLOCKED

    # New payment failure ingested while blocked unblocks to OPEN
    res = OpportunityStateMachine.handle_payment_failed(opp)
    assert res.transitioned
    assert opp.current_state == OpportunityState.OPEN


def test_state_machine_link_expired_scenarios(sample_opportunity: RecoveryOpportunity) -> None:
    opp = sample_opportunity
    OpportunityStateMachine.transition(opp, OpportunityState.IN_EVALUATION)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_SCHEDULED)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_EXECUTING)
    OpportunityStateMachine.transition(opp, OpportunityState.AWAITING_SETTLEMENT)

    # Case 1: Retry budget remains -> resets to OPEN
    res1 = OpportunityStateMachine.handle_payment_link_expired(opp, retry_budget_remaining=True)
    assert res1.transitioned
    assert opp.current_state == OpportunityState.OPEN

    # Advance back to AWAITING_SETTLEMENT
    OpportunityStateMachine.transition(opp, OpportunityState.IN_EVALUATION)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_SCHEDULED)
    OpportunityStateMachine.transition(opp, OpportunityState.ACTION_EXECUTING)
    OpportunityStateMachine.transition(opp, OpportunityState.AWAITING_SETTLEMENT)

    # Case 2: Retry budget exhausted -> EXPIRED (Terminal)
    res2 = OpportunityStateMachine.handle_payment_link_expired(opp, retry_budget_remaining=False)
    assert res2.transitioned
    assert opp.current_state == OpportunityState.EXPIRED

    # Cannot transition out of EXPIRED
    with pytest.raises(TerminalStateMutationError):
        OpportunityStateMachine.transition(opp, OpportunityState.OPEN)

    # Calling handle_payment_captured on EXPIRED raises TerminalStateMutationError
    with pytest.raises(TerminalStateMutationError):
        OpportunityStateMachine.handle_payment_captured(opp)

    # Calling handle_payment_failed on EXPIRED produces TERMINAL_FAILURE_IGNORED
    res_fail = OpportunityStateMachine.handle_payment_failed(opp)
    assert res_fail.event_name == "TERMINAL_FAILURE_IGNORED"
    assert res_fail.suppressed

    # Calling handle_payment_link_expired on EXPIRED produces TERMINAL_EVENT_IGNORED
    res_exp = OpportunityStateMachine.handle_payment_link_expired(opp, retry_budget_remaining=True)
    assert res_exp.event_name == "TERMINAL_EVENT_IGNORED"
    assert res_exp.suppressed

    # Calling handle_payment_link_expired on OPEN (unhandled state) returns transitioned=False
    opp_open = opp.model_copy()
    opp_open.current_state = OpportunityState.OPEN
    res_open = OpportunityStateMachine.handle_payment_link_expired(
        opp_open, retry_budget_remaining=True
    )
    assert not res_open.transitioned
