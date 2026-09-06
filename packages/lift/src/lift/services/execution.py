"""Atomic Execution Safety Gate and 3-Phase Execution Coordinator.

This service orchestrates the three-phase execution protocol:
  Phase 1: Lock & Evaluate Intent (pessimistic row locks held < 5ms).
  Phase 2: External Side-Effect Dispatch (NO database locks held).
  Phase 3: Fenced Settlement & Task Completion (task row lock verifies current ownership).

CRITICAL INVARIANTS:
1. Phase 3 settlement verifies current task ownership:
   task.lease_version == worker.claimed_lease_version
   AND task.locked_by == worker.worker_id
   AND task.status == 'RUNNING'
   If ownership check fails: perform NO mutations, rollback, emit STALE_WORKER_FENCED.
2. Before calling create_payment_link, query deterministic reference_id first.
   If a link exists, backfill and reconcile without creating a second link.
3. CLAIMED execution reserves 1 contact slot. If definitively FAILED, slot is released.
   Zero shared-counter decrements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lift.core.constants import DEFAULT_BETA, DEFAULT_LAMBDA_FRICTION
from lift.core.errors import (
    GatewayError,
    GatewayResourceNotFoundError,
    GatewayTimeoutError,
    StaleWorkerFencedError,
)
from lift.core.types import (
    DecisionType,
    ExecutionStatus,
    InterventionType,
    OpportunityState,
)
from lift.domain.models import Merchant
from lift.domain.state_machine import OpportunityStateMachine
from lift.economics.priors import get_global_prior
from lift.gateway.interface import PaymentGatewayAdapter
from lift.gateway.types import GatewayCustomerInfo
from lift.services.evaluation import InterventionEvaluationService
from lift.services.policy_gate import PolicyGateService
from lift.services.voucher import generate_idempotency_key
from lift.storage.base import utc_now
from lift.storage.mappers import to_customer_domain, to_opportunity_domain
from lift.storage.orm_models import (
    ExecutionRecordORM,
    MerchantORM,
    RecoveryDecisionORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.storage.repositories.customer import CustomerRepository
from lift.storage.repositories.opportunity import OpportunityRepository
from lift.storage.repositories.task import TaskQueueRepository
from lift.storage.repositories.voucher import ExecutionRecordRepository
from lift.webhooks.reference import generate_reference_id

logger = logging.getLogger(__name__)


class Phase2OutcomeType(StrEnum):
    SUCCESS = "SUCCESS"
    HARD_FAILURE = "HARD_FAILURE"
    UNKNOWN_OR_RETRYABLE = "UNKNOWN_OR_RETRYABLE"
    SKIPPED_ALREADY_EXISTS = "SKIPPED_ALREADY_EXISTS"


@dataclass(frozen=True)
class Phase1Result:
    action_required: bool
    should_dispatch: bool
    opportunity_id: UUID
    voucher_id: UUID | None = None
    reference_id: str | None = None
    attempt_index: int | None = None
    amount_subunits: int = 0
    currency: str = "INR"
    customer_info: GatewayCustomerInfo | None = None
    scheduled_retry_at: datetime | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class Phase2Result:
    outcome_type: Phase2OutcomeType
    external_reference_id: str | None = None
    error_message: str | None = None
    payment_link_status: str | None = None


class ExecutionSafetyService:
    """Coordinates atomic execution safety gate across Phase 1, Phase 2, and Phase 3."""

    def __init__(
        self,
        session: Session,
        gateway: PaymentGatewayAdapter,
        evaluation_service: InterventionEvaluationService | None = None,
        policy_gate_service: PolicyGateService | None = None,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.evaluation_service = evaluation_service or InterventionEvaluationService(
            lambda_friction=DEFAULT_LAMBDA_FRICTION, beta=DEFAULT_BETA
        )
        self.policy_gate_service = policy_gate_service or PolicyGateService()
        self.task_repo = TaskQueueRepository(session)
        self.opp_repo = OpportunityRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.voucher_repo = ExecutionRecordRepository(session)

    # -------------------------------------------------------------------------
    # PHASE 1: Lock & Evaluate Intent (Pessimistic Row Locks Held < 5ms)
    # -------------------------------------------------------------------------
    def execute_phase_1(
        self,
        opportunity_id: UUID,
        task_id: UUID,
        claimed_lease_version: int,
        worker_id: str,
        eval_time: datetime | None = None,
    ) -> Phase1Result:
        """Execute Phase 1 within caller's or local transaction.

        Acquires row locks: customers first, then recovery_opportunities.
        Validates opportunity state, checks policy gate, evaluates NIRV slate.
        If outreach is selected: creates CLAIMED voucher (reserving contact slot),
        advances opportunity to ACTION_EXECUTING, and commits Phase 1.
        """
        now = eval_time or utc_now()

        # 1. Fetch opportunity to find merchant & customer
        opp_stmt = self.opp_repo.get_by_id(opportunity_id)
        if not opp_stmt:
            logger.warning("Phase 1 aborted: opportunity %s not found", opportunity_id)
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="OPPORTUNITY_NOT_FOUND",
            )

        # Check terminal state before acquiring locks
        if opp_stmt.current_state in (
            OpportunityState.RECOVERED,
            OpportunityState.EXPIRED,
            OpportunityState.TERMINATED,
        ):
            logger.info(
                "Phase 1: Opportunity %s already in terminal state %s",
                opportunity_id,
                opp_stmt.current_state.value,
            )
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason=f"TERMINAL_STATE_{opp_stmt.current_state.value}",
            )

        # 2. Acquire row locks in strict hierarchical order: customers -> recovery_opportunities
        cust_orm = self.customer_repo.lock_for_update(opp_stmt.customer_id)
        opp_orm = self.opp_repo.lock_for_update(opportunity_id)

        if not cust_orm or not opp_orm:
            logger.error(
                "Phase 1: Failed to acquire row locks for opp=%s, cust=%s",
                opportunity_id,
                opp_stmt.customer_id,
            )
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="LOCK_ACQUISITION_FAILED",
            )

        # Re-verify state under lock
        current_state = OpportunityState(opp_orm.current_state)
        if current_state == OpportunityState.RECOVERED:
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="ALREADY_RECOVERED",
            )
        if current_state not in (
            OpportunityState.OPEN,
            OpportunityState.ACTION_SCHEDULED,
        ):
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason=f"INVALID_STATE_{current_state.value}",
            )

        # 3. Transition to IN_EVALUATION
        opp_domain = to_opportunity_domain(opp_orm)
        cust_domain = to_customer_domain(cust_orm)
        OpportunityStateMachine.transition(
            opp_domain, OpportunityState.IN_EVALUATION, reason="worker_evaluation"
        )
        opp_orm.current_state = OpportunityState.IN_EVALUATION.value
        opp_orm.last_evaluated_at = now
        self.session.flush()

        # 4. Fetch merchant for timezone and idempotency salt
        merchant_orm = self.session.get(MerchantORM, opp_orm.merchant_id)
        if not merchant_orm:
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="MERCHANT_NOT_FOUND",
            )
        merchant_domain = Merchant(
            id=merchant_orm.id,
            name=merchant_orm.name,
            default_currency=merchant_orm.default_currency,
            timezone=merchant_orm.timezone,
            idempotency_salt=merchant_orm.idempotency_salt,
        )

        # 5. Derive active 7-day contacts from immutable execution records
        active_contacts_7d = self.customer_repo.count_active_contacts_7d(cust_orm.id)
        cust_domain.rolling_contacts_7d = active_contacts_7d

        # 6. Evaluate Candidate Slate
        prior_recovery_rate = get_global_prior(opp_domain.failure_category)

        candidate_types = [
            InterventionType.DIRECT_PAYMENT_LINK_SMS,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP,
            InterventionType.INTERNAL_RETRY_SCHEDULE,
            InterventionType.NO_ACTION,
        ]

        evaluated_candidates = []
        for itype in candidate_types:
            if itype in (InterventionType.INTERNAL_RETRY_SCHEDULE, InterventionType.NO_ACTION):
                # Non-contact interventions do not receive direct customer outreach uplift
                cand_p_recovery = opp_domain.organic_recovery_estimate
            else:
                # Direct customer outreach provides uplift over organic base
                cand_p_recovery = min(0.95, prior_recovery_rate * 1.5)

            cand = self.evaluation_service.evaluate_single_candidate(
                opportunity=opp_domain,
                customer=cust_domain,
                intervention_type=itype,
                p_recovery=cand_p_recovery,
                p_organic=opp_domain.organic_recovery_estimate,
                confidence_score=0.85,
                eval_time=now,
            )
            decision = self.policy_gate_service.evaluate_candidate(
                candidate=cand,
                opportunity=opp_domain,
                merchant=merchant_domain,
                customer=cust_domain,
                eval_time=now,
            )
            if decision.decision_type == DecisionType.AUTHORIZED:
                evaluated_candidates.append((cand, decision))

        # Select highest NIRV authorized candidate
        if not evaluated_candidates:
            # All candidates blocked or no positive NIRV
            opp_orm.current_state = OpportunityState.ACTION_BLOCKED.value
            decision_orm = RecoveryDecisionORM(
                id=uuid4(),
                opportunity_id=opportunity_id,
                selected_candidate_id=None,
                decision_type=DecisionType.BLOCKED.value,
                policy_evaluation_details={"reason": "NO_AUTHORIZED_POSITIVE_NIRV_CANDIDATES"},
                blocked_reason_code="NO_AUTHORIZED_ACTION",
                explanation="All candidate interventions blocked by policy gate or negative NIRV.",
                decided_at=now,
            )
            self.session.add(decision_orm)
            self.session.flush()
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="ALL_CANDIDATES_BLOCKED",
            )

        best_cand, best_decision = max(
            evaluated_candidates, key=lambda pair: pair[0].expected_net_value_subunits
        )

        # Handle Internal Retry Scheduled
        if best_cand.intervention_type == InterventionType.INTERNAL_RETRY_SCHEDULE:
            opp_orm.current_state = OpportunityState.ACTION_SCHEDULED.value
            # Schedule next evaluation 4 hours later
            from datetime import timedelta

            scheduled_at = now + timedelta(hours=4)
            self.task_repo.enqueue_task(
                task_type="EVALUATE_OPPORTUNITY",
                payload={"opportunity_id": str(opportunity_id)},
                scheduled_at=scheduled_at,
            )
            decision_orm = RecoveryDecisionORM(
                id=uuid4(),
                opportunity_id=opportunity_id,
                selected_candidate_id=None,
                decision_type=DecisionType.AUTHORIZED.value,
                policy_evaluation_details=best_decision.policy_evaluation_details,
                explanation="Internal retry scheduled.",
                decided_at=now,
            )
            self.session.add(decision_orm)
            self.session.flush()
            return Phase1Result(
                action_required=True,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                scheduled_retry_at=scheduled_at,
            )

        if best_cand.intervention_type == InterventionType.NO_ACTION:
            opp_orm.current_state = OpportunityState.ACTION_BLOCKED.value
            decision_orm = RecoveryDecisionORM(
                id=uuid4(),
                opportunity_id=opportunity_id,
                selected_candidate_id=None,
                decision_type=DecisionType.NO_ACTION.value,
                policy_evaluation_details=best_decision.policy_evaluation_details,
                explanation="No action selected by optimizer.",
                decided_at=now,
            )
            self.session.add(decision_orm)
            self.session.flush()
            return Phase1Result(
                action_required=False,
                should_dispatch=False,
                opportunity_id=opportunity_id,
                skip_reason="NO_ACTION_SELECTED",
            )

        # Active outreach selected: allocate attempt index
        opp_orm.total_interventions_count += 1
        opp_orm.total_contacts_count += 1
        attempt_index = opp_orm.total_interventions_count

        # Persist decision
        decision_id = uuid4()
        decision_orm = RecoveryDecisionORM(
            id=decision_id,
            opportunity_id=opportunity_id,
            selected_candidate_id=None,
            decision_type=DecisionType.AUTHORIZED.value,
            policy_evaluation_details=best_decision.policy_evaluation_details,
            explanation=(
                f"Authorized {best_cand.intervention_type.value} "
                f"with NIRV={best_cand.expected_net_value_subunits}"
            ),
            decided_at=now,
        )
        self.session.add(decision_orm)
        self.session.flush()

        # Generate canonical reference_id and idempotency key
        reference_id = generate_reference_id(opportunity_id, attempt_index)
        idempotency_key = generate_idempotency_key(
            opportunity_id=opportunity_id,
            intervention_type=best_cand.intervention_type,
            attempt_index=attempt_index,
            merchant_salt=merchant_orm.idempotency_salt,
        )

        # Create Execution Record voucher (Conservative Contact Reservation)
        voucher_id = uuid4()
        voucher_orm = ExecutionRecordORM(
            id=voucher_id,
            decision_id=decision_id,
            attempt_index=attempt_index,
            idempotency_key=idempotency_key,
            reference_id=reference_id,
            intervention_type=best_cand.intervention_type.value,
            execution_status=ExecutionStatus.CLAIMED.value,
            external_reference_id=None,
            failure_message=None,
            claimed_at=now,
            executed_at=None,
            task_id=task_id,
            lease_version=claimed_lease_version,
        )
        self.session.add(voucher_orm)

        # Advance opportunity to ACTION_EXECUTING
        opp_orm.current_state = OpportunityState.ACTION_EXECUTING.value
        opp_orm.execution_claimed_at = now
        self.session.flush()

        # Prepare gateway customer info
        customer_info = GatewayCustomerInfo(
            contact=f"+91{cust_orm.external_customer_id[-10:]}"
            if len(cust_orm.external_customer_id) >= 10
            else None
        )

        return Phase1Result(
            action_required=True,
            should_dispatch=True,
            opportunity_id=opportunity_id,
            voucher_id=voucher_id,
            reference_id=reference_id,
            attempt_index=attempt_index,
            amount_subunits=opp_orm.amount_at_risk_subunits,
            currency=opp_orm.currency,
            customer_info=customer_info,
        )

    # -------------------------------------------------------------------------
    # PHASE 2: Out-of-Transaction External Dispatch (NO DB LOCKS HELD)
    # -------------------------------------------------------------------------
    def execute_phase_2(
        self,
        phase_1_result: Phase1Result,
    ) -> Phase2Result:
        """Execute Phase 2 HTTP calls with zero database locks held.

        Pre-call check: Query deterministic reference_id first.
        If a Payment Link already exists on Razorpay, backfill it without creating a duplicate.
        """
        assert phase_1_result.reference_id is not None
        ref_id = phase_1_result.reference_id

        # 1. Pre-call reconciliation (Guardrail 4)
        try:
            existing = self.gateway.fetch_payment_link_by_reference_id(ref_id)
            if existing is not None:
                logger.info(
                    "Phase 2 pre-check discovered existing Payment Link %s for ref_id %s",
                    existing.id,
                    ref_id,
                )
                return Phase2Result(
                    outcome_type=Phase2OutcomeType.SUCCESS,
                    external_reference_id=existing.id,
                    payment_link_status=existing.status,
                )
        except GatewayResourceNotFoundError:
            # Confirmed absence: safe to proceed to creation
            pass
        except (GatewayTimeoutError, TimeoutError) as err:
            logger.warning(
                "Phase 2 pre-call reconciliation check timed out for %s: %s", ref_id, err
            )
            return Phase2Result(
                outcome_type=Phase2OutcomeType.UNKNOWN_OR_RETRYABLE,
                error_message=f"Pre-call lookup timed out: {err}",
            )
        except GatewayError as err:
            err_str = str(err)
            status_code = err.details.get("status_code") if isinstance(err.details, dict) else None
            status_retryable = isinstance(status_code, int) and (
                status_code in (408, 429) or status_code >= 500
            )
            # Check for ambiguous/retryable gateway errors (network error, 408, 429, 5xx)
            if (
                err.gateway_code in ("GATEWAY_TIMEOUT", "NETWORK_ERROR")
                or status_retryable
                or "timeout" in err_str.lower()
            ):
                logger.warning(
                    "Phase 2 pre-call reconciliation check encountered ambiguous error for %s: %s",
                    ref_id,
                    err,
                )
                return Phase2Result(
                    outcome_type=Phase2OutcomeType.UNKNOWN_OR_RETRYABLE,
                    error_message=f"Pre-call lookup ambiguous: {err}",
                )
            logger.error(
                "Phase 2 pre-call reconciliation check failed with hard error for %s: %s",
                ref_id,
                err,
            )
            return Phase2Result(
                outcome_type=Phase2OutcomeType.HARD_FAILURE,
                error_message=f"Pre-call lookup error: {err}",
            )
        except Exception as err:
            logger.error(
                "Phase 2 pre-call reconciliation check raised unexpected error for %s: %s",
                ref_id,
                err,
            )
            return Phase2Result(
                outcome_type=Phase2OutcomeType.UNKNOWN_OR_RETRYABLE,
                error_message=f"Pre-call lookup unexpected failure: {err}",
            )

        # 2. Dispatch create_payment_link
        notes = {
            "opportunity_id": str(phase_1_result.opportunity_id),
            "attempt_index": str(phase_1_result.attempt_index or 1),
        }

        try:
            link = self.gateway.create_payment_link(
                amount_subunits=phase_1_result.amount_subunits,
                currency=phase_1_result.currency,
                reference_id=ref_id,
                description="Order Recovery Payment Link",
                customer=phase_1_result.customer_info or GatewayCustomerInfo(),
                notes=notes,
            )
            return Phase2Result(
                outcome_type=Phase2OutcomeType.SUCCESS,
                external_reference_id=link.id,
                payment_link_status=link.status,
            )
        except Exception as err:
            err_str = str(err)
            logger.error("Phase 2 dispatch failed for ref_id %s: %s", ref_id, err)
            # Differentiate hard client errors from transient/timeout
            if isinstance(err, GatewayTimeoutError) or "timeout" in err_str.lower():
                return Phase2Result(
                    outcome_type=Phase2OutcomeType.UNKNOWN_OR_RETRYABLE,
                    error_message=err_str,
                )
            if "400" in err_str or "bad_request" in err_str.lower():
                return Phase2Result(
                    outcome_type=Phase2OutcomeType.HARD_FAILURE,
                    error_message=err_str,
                )
            return Phase2Result(
                outcome_type=Phase2OutcomeType.UNKNOWN_OR_RETRYABLE,
                error_message=err_str,
            )

    # -------------------------------------------------------------------------
    # PHASE 3: Settle Execution & Fenced Task Completion (Choice B Task Lock)
    # -------------------------------------------------------------------------
    def execute_phase_3(
        self,
        task_id: UUID,
        claimed_lease_version: int,
        worker_id: str,
        voucher_id: UUID,
        opportunity_id: UUID,
        phase_2_result: Phase2Result,
    ) -> bool:
        """Execute Phase 3 within an isolated database transaction.

        MANDATORY GUARDRAIL 1:
        Pessimistically locks task_queue row and verifies:
          task.lease_version == claimed_lease_version
          AND task.locked_by == worker_id
          AND task.status == 'RUNNING'

        If mismatch:
          - performs NO execution_records mutation
          - performs NO recovery_opportunities mutation
          - performs NO task mutation
          - rolls back transaction immediately
          - emits STALE_WORKER_FENCED
          - raises StaleWorkerFencedError
        """
        # Step 1: Pessimistically lock task_queue row
        bind = self.session.get_bind()
        stmt = self.session.query(TaskQueueORM).filter(TaskQueueORM.id == task_id)
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task_orm = stmt.first()

        # Step 2: Verify current task ownership
        if (
            not task_orm
            or task_orm.lease_version != claimed_lease_version
            or task_orm.locked_by != worker_id
            or task_orm.status != "RUNNING"
        ):
            current_lease = task_orm.lease_version if task_orm else None
            logger.warning(
                "STALE_WORKER_FENCED: Worker %s holding lease_version=%s "
                "lost ownership of task %s (current_lease=%s, status=%s, locked_by=%s)",
                worker_id,
                claimed_lease_version,
                task_id,
                current_lease,
                task_orm.status if task_orm else None,
                task_orm.locked_by if task_orm else None,
            )
            # Perform NO mutations; rollback caller transaction
            self.session.rollback()
            raise StaleWorkerFencedError(task_id, claimed_lease_version, current_lease)

        # Step 3: Ownership verified! Apply mutations based on Phase 2 outcome
        voucher_orm = self.voucher_repo.get_by_id_orm(voucher_id)
        opp_orm = self.session.get(RecoveryOpportunityORM, opportunity_id)

        now = utc_now()

        if phase_2_result.outcome_type == Phase2OutcomeType.SUCCESS:
            if voucher_orm and voucher_orm.execution_status == ExecutionStatus.CLAIMED.value:
                voucher_orm.execution_status = ExecutionStatus.EXECUTED.value
                voucher_orm.external_reference_id = phase_2_result.external_reference_id
                voucher_orm.executed_at = now

            if opp_orm and opp_orm.current_state == OpportunityState.ACTION_EXECUTING.value:
                opp_orm.current_state = OpportunityState.AWAITING_SETTLEMENT.value

            # Mark task COMPLETED
            task_orm.status = "COMPLETED"
            task_orm.locked_by = None
            task_orm.locked_at = None
            self.session.flush()
            return True

        elif phase_2_result.outcome_type == Phase2OutcomeType.HARD_FAILURE:
            # Definitively failed: release contact slot
            if voucher_orm:
                voucher_orm.execution_status = ExecutionStatus.FAILED.value
                voucher_orm.failure_message = phase_2_result.error_message

            if opp_orm and opp_orm.current_state == OpportunityState.ACTION_EXECUTING.value:
                opp_orm.current_state = OpportunityState.OPEN.value

            task_orm.attempts += 1
            task_orm.status = "FAILED"
            task_orm.last_error = phase_2_result.error_message
            task_orm.locked_by = None
            task_orm.locked_at = None
            self.session.flush()
            return True

        else:
            # UNKNOWN outcome: mark voucher and opportunity RECONCILIATION_REQUIRED
            if voucher_orm:
                voucher_orm.execution_status = ExecutionStatus.RECONCILIATION_REQUIRED.value
                voucher_orm.failure_message = phase_2_result.error_message

            if opp_orm and opp_orm.current_state == OpportunityState.ACTION_EXECUTING.value:
                opp_orm.current_state = OpportunityState.RECONCILIATION_REQUIRED.value

            # Enqueue dedicated RECONCILE_PAYMENT_LINK task
            self.task_repo.enqueue_task(
                task_type="RECONCILE_PAYMENT_LINK",
                payload={
                    "opportunity_id": str(opportunity_id),
                    "voucher_id": str(voucher_id),
                    "reference_id": voucher_orm.reference_id if voucher_orm else None,
                },
                priority=5,
            )

            task_orm.status = "FAILED"
            task_orm.last_error = phase_2_result.error_message
            task_orm.locked_by = None
            task_orm.locked_at = None
            self.session.flush()
            return True
