"""Task handler for RECONCILE_PAYMENT tasks."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lift.core.errors import GatewayResourceNotFoundError, GatewayTimeoutError
from lift.core.types import OpportunityState
from lift.gateway.interface import PaymentGatewayAdapter
from lift.storage.base import utc_now
from lift.storage.orm_models import (
    PaymentAttemptORM,
    PaymentEvidenceORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.storage.repositories.task import TaskQueueRepository

logger = logging.getLogger(__name__)


def handle_reconcile_payment(
    session: Session,
    task: TaskQueueORM,
    gateway: PaymentGatewayAdapter,
    worker_id: str,
    claimed_lease_version: int,
) -> bool:
    """Execute authoritative external reconciliation of a payment transaction.

    STRICT PAYMENT AUTHORITY INVARIANT:
    payment.authorized != RECOVERED
    Only captured payments transition an opportunity to RECOVERED.
    """
    payload = task.payload
    payment_id = payload.get("payment_id")
    raw_opp_id = payload.get("opportunity_id")
    opportunity_id = UUID(str(raw_opp_id)) if raw_opp_id else None

    task_repo = TaskQueueRepository(session)

    if not payment_id:
        logger.error("Task %s missing payment_id", task.id)
        failed = task_repo.fail_task_permanently(
            task.id, claimed_lease_version, worker_id, "Missing payment_id"
        )
        if not failed:
            session.rollback()
            return False
        session.commit()
        return False

    # 1. Fetch payment details from gateway
    try:
        gw_payment = gateway.fetch_payment(payment_id)
        current_status = gw_payment.status.lower()
    except GatewayResourceNotFoundError:
        logger.warning("Payment %s not found on gateway", payment_id)
        failed = task_repo.fail_task_permanently(
            task.id, claimed_lease_version, worker_id, "Payment not found"
        )
        if not failed:
            session.rollback()
            return False
        session.commit()
        return False
    except (GatewayTimeoutError, TimeoutError) as err:
        logger.warning("Timeout fetching payment %s: %s", payment_id, err)
        retried = task_repo.retry_task(
            task.id,
            claimed_lease_version,
            worker_id,
            str(err),
            utc_now() + timedelta(seconds=20),
        )
        if not retried:
            session.rollback()
            return False
        session.commit()
        return False
    except Exception as err:
        logger.error("Error fetching payment %s: %s", payment_id, err)
        retried = task_repo.retry_task(
            task.id,
            claimed_lease_version,
            worker_id,
            str(err),
            utc_now() + timedelta(seconds=30),
        )
        if not retried:
            session.rollback()
            return False
        session.commit()
        return False

    # Step 1: Lock task_queue row and verify current lease ownership BEFORE mutating domain state
    stmt = session.query(TaskQueueORM).filter(TaskQueueORM.id == task.id)
    bind = session.get_bind()
    if not (bind and bind.dialect.name == "sqlite"):
        stmt = stmt.with_for_update()
    current_task = stmt.first()

    if (
        not current_task
        or current_task.lease_version != claimed_lease_version
        or current_task.locked_by != worker_id
        or current_task.status != "RUNNING"
    ):
        logger.warning(
            "STALE_WORKER_FENCED: Worker %s holding lease_version=%s "
            "lost ownership of payment reconciliation task %s. Rolling back with zero mutations.",
            worker_id,
            claimed_lease_version,
            task.id,
        )
        session.rollback()
        return False

    # 2. Update PaymentAttempt ORM record
    attempt_orm = (
        session.query(PaymentAttemptORM)
        .filter(PaymentAttemptORM.razorpay_payment_id == payment_id)
        .first()
    )
    if attempt_orm:
        attempt_orm.status = current_status
        if gw_payment.error_code:
            attempt_orm.error_code = gw_payment.error_code
            attempt_orm.error_description = gw_payment.error_description

    # 3. Apply Strict Authority Rule to Recovery Opportunity
    now = utc_now()
    opp_orm = session.get(RecoveryOpportunityORM, opportunity_id) if opportunity_id else None
    if not opp_orm and attempt_orm and attempt_orm.recovery_opportunity_id:
        opp_orm = session.get(RecoveryOpportunityORM, attempt_orm.recovery_opportunity_id)

    if opp_orm:
        if current_status == "captured":
            # Authoritative Proof of Recovery
            logger.info(
                "Payment %s is CAPTURED: transitioning opp %s to RECOVERED", payment_id, opp_orm.id
            )
            if opp_orm.current_state != OpportunityState.RECOVERED.value:
                opp_orm.current_state = OpportunityState.RECOVERED.value
                opp_orm.closed_at = now

            # Record Payment Evidence honestly distinguishing authenticated gateway fetch
            # from webhook HMAC
            existing_evidence = (
                session.query(PaymentEvidenceORM)
                .filter(PaymentEvidenceORM.razorpay_payment_id == payment_id)
                .first()
            )
            if not existing_evidence:
                evidence = PaymentEvidenceORM(
                    id=uuid4(),
                    opportunity_id=opp_orm.id,
                    razorpay_payment_id=payment_id,
                    event_type="payment.captured",
                    signature_hash=f"reconciled_gateway_fetch:{payment_id}",
                    captured_amount_subunits=gw_payment.amount,
                    verified_at=now,
                )
                session.add(evidence)

        elif current_status == "authorized":
            # Intermediate authorization: MUST NOT mark RECOVERED
            logger.info("Payment %s is AUTHORIZED: remaining in AWAITING_SETTLEMENT", payment_id)
            if opp_orm.current_state not in (
                OpportunityState.RECOVERED.value,
                OpportunityState.AWAITING_SETTLEMENT.value,
            ):
                opp_orm.current_state = OpportunityState.AWAITING_SETTLEMENT.value

        elif current_status == "failed":
            logger.info("Payment %s is FAILED", payment_id)

    current_task.status = "COMPLETED"
    current_task.locked_by = None
    current_task.locked_at = None
    session.commit()
    return True
