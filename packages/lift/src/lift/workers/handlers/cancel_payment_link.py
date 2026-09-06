"""Task handler for CANCEL_PAYMENT_LINK tasks."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from lift.core.errors import GatewayResourceNotFoundError, GatewayTimeoutError
from lift.core.types import OpportunityState
from lift.gateway.interface import PaymentGatewayAdapter
from lift.storage.base import utc_now
from lift.storage.orm_models import (
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.storage.repositories.task import TaskQueueRepository
from lift.storage.repositories.voucher import ExecutionRecordRepository

logger = logging.getLogger(__name__)


def handle_cancel_payment_link(
    session: Session,
    task: TaskQueueORM,
    gateway: PaymentGatewayAdapter,
    worker_id: str,
    claimed_lease_version: int,
) -> bool:
    """Execute CANCEL_PAYMENT_LINK task with accurate gateway status handling.

    Distinguishes:
    - 'cancelled': idempotent success -> mark task COMPLETED
    - 'paid': payment recovery evidence, NOT cancellation success -> transition to RECOVERED
    - 'partially_paid': reconciliation required
    - 'expired': terminal external state -> mark task COMPLETED
    - 5xx / 429: retry with backoff
    - timeout / UNKNOWN: enqueue RECONCILE_PAYMENT_LINK
    """
    payload = task.payload
    plink_id = payload.get("payment_link_id")
    raw_opp_id = payload.get("opportunity_id")
    opportunity_id = UUID(str(raw_opp_id)) if raw_opp_id else None

    voucher_repo = ExecutionRecordRepository(session)
    task_repo = TaskQueueRepository(session)

    # If plink_id is not directly given, locate it from opportunity active vouchers
    if not plink_id and opportunity_id:
        active_vouchers = voucher_repo.get_active_for_opportunity(opportunity_id)
        for v in active_vouchers:
            if v.external_reference_id and v.external_reference_id.startswith("plink_"):
                plink_id = v.external_reference_id
                break

    if not plink_id:
        logger.warning(
            "Task %s: No active payment_link_id found to cancel for opp=%s",
            task.id,
            opportunity_id,
        )
        completed = task_repo.complete_task(task.id, claimed_lease_version, worker_id)
        if not completed:
            session.rollback()
            return False
        session.commit()
        return True

    # 1. Fetch current external status of the payment link from gateway
    try:
        link_status = gateway.fetch_payment_link(plink_id)
        current_status = link_status.status.lower()
    except GatewayResourceNotFoundError:
        logger.info(
            "Payment Link %s not found on gateway; treating as already cancelled/removed", plink_id
        )
        current_status = "cancelled"
    except (GatewayTimeoutError, TimeoutError) as err:
        logger.warning("Timeout querying payment link %s: %s", plink_id, err)
        retried = task_repo.retry_task(
            task.id,
            claimed_lease_version,
            worker_id,
            str(err),
            utc_now() + timedelta(seconds=15),
        )
        if not retried:
            session.rollback()
            return False
        session.commit()
        return False
    except Exception as err:
        logger.error("Error querying payment link %s: %s", plink_id, err)
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
            "lost ownership of cancel task %s. Rolling back with zero mutations.",
            worker_id,
            claimed_lease_version,
            task.id,
        )
        session.rollback()
        return False

    # 2. Strict status handling
    if current_status == "paid":
        # Link was paid! Treat as recovery evidence, NOT cancellation success.
        logger.info("Payment Link %s is already PAID; handling as recovery evidence", plink_id)
        if opportunity_id:
            opp_orm = session.get(RecoveryOpportunityORM, opportunity_id)
            if opp_orm and opp_orm.current_state != OpportunityState.RECOVERED.value:
                opp_orm.current_state = OpportunityState.RECOVERED.value
                opp_orm.closed_at = utc_now()

        current_task.status = "COMPLETED"
        current_task.locked_by = None
        current_task.locked_at = None
        session.commit()
        return True

    elif current_status == "cancelled":
        logger.info("Payment Link %s is already cancelled; idempotent success", plink_id)
        current_task.status = "COMPLETED"
        current_task.locked_by = None
        current_task.locked_at = None
        session.commit()
        return True

    elif current_status == "expired":
        logger.info("Payment Link %s is expired on gateway; terminal state", plink_id)
        current_task.status = "COMPLETED"
        current_task.locked_by = None
        current_task.locked_at = None
        session.commit()
        return True

    elif current_status == "partially_paid":
        logger.warning(
            "Payment Link %s is partially_paid; marking reconciliation required", plink_id
        )
        if opportunity_id:
            opp_orm = session.get(RecoveryOpportunityORM, opportunity_id)
            if opp_orm:
                opp_orm.current_state = OpportunityState.RECONCILIATION_REQUIRED.value
        current_task.status = "COMPLETED"
        current_task.locked_by = None
        current_task.locked_at = None
        session.commit()
        return True

    # Otherwise status is 'created' -> Attempt cancellation
    try:
        success = gateway.cancel_payment_link(plink_id)
        if success:
            logger.info("Payment Link %s cancelled successfully", plink_id)
            current_task.status = "COMPLETED"
            current_task.locked_by = None
            current_task.locked_at = None
            session.commit()
            return True
        else:
            retried = task_repo.retry_task(
                task.id,
                claimed_lease_version,
                worker_id,
                "Gateway returned False for cancellation",
                utc_now() + timedelta(seconds=15),
            )
            if not retried:
                session.rollback()
                return False
            session.commit()
            return False
    except Exception as err:
        logger.error("Failed to cancel payment link %s: %s", plink_id, err)
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
