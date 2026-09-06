"""Task handler for RECONCILE_PAYMENT_LINK tasks."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from lift.core.errors import GatewayResourceNotFoundError, GatewayTimeoutError
from lift.core.types import ExecutionStatus, OpportunityState
from lift.gateway.interface import PaymentGatewayAdapter
from lift.storage.base import utc_now
from lift.storage.orm_models import (
    ExecutionRecordORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.storage.repositories.task import TaskQueueRepository
from lift.storage.repositories.voucher import ExecutionRecordRepository

logger = logging.getLogger(__name__)


def handle_reconcile_payment_link(
    session: Session,
    task: TaskQueueORM,
    gateway: PaymentGatewayAdapter,
    worker_id: str,
    claimed_lease_version: int,
) -> bool:
    """Execute authoritative external reconciliation of a Payment Link.

    INVARIANTS:
    1. Active Dispatch Protection: If the original dispatch task lease is still
       active and unexpired, abort and do not take over.
    2. Uses its OWN reconciliation task lease for all fenced mutations.
    3. Absence Semantics: 404/not-found means no currently discoverable link,
       NOT historical proof that request never reached Razorpay.
       Marks voucher FAILED (releases contact slot), resets opportunity to OPEN.
    4. Paid link transitions opportunity to RECOVERED.
    """
    payload = task.payload
    raw_opp_id = payload.get("opportunity_id")
    raw_voucher_id = payload.get("voucher_id")
    reference_id = payload.get("reference_id")
    plink_id = payload.get("payment_link_id")

    task_repo = TaskQueueRepository(session)
    voucher_repo = ExecutionRecordRepository(session)

    opp_id = UUID(str(raw_opp_id)) if raw_opp_id else None
    voucher_id = UUID(str(raw_voucher_id)) if raw_voucher_id else None

    # Locate voucher and opportunity if needed
    voucher_orm = session.get(ExecutionRecordORM, voucher_id) if voucher_id else None
    if not voucher_orm and reference_id:
        voucher_domain = voucher_repo.get_by_reference_id(reference_id)
        if voucher_domain:
            voucher_orm = session.get(ExecutionRecordORM, voucher_domain.id)

    if voucher_orm and not reference_id:
        reference_id = voucher_orm.reference_id

    # -------------------------------------------------------------------------
    # Guardrail 3: Active Dispatch Protection
    # -------------------------------------------------------------------------
    if voucher_orm and voucher_orm.task_id:
        original_dispatch_task = session.get(TaskQueueORM, voucher_orm.task_id)
        if original_dispatch_task and original_dispatch_task.status == "RUNNING":
            now = utc_now()
            if original_dispatch_task.locked_at:
                locked_at = original_dispatch_task.locked_at
                if locked_at.tzinfo is None:
                    from datetime import timezone

                    locked_at = locked_at.replace(tzinfo=timezone.utc)
                if (now - locked_at).total_seconds() < 60:
                    logger.info(
                        "Reconciliation skipped: original dispatch task %s "
                        "still holds an active lease (< 60s)",
                        voucher_orm.task_id,
                    )
                    # Reschedule reconciliation check after lease expiry
                    retried = task_repo.retry_task(
                        task.id,
                        claimed_lease_version,
                        worker_id,
                        "Original dispatch lease active",
                        now + timedelta(seconds=20),
                    )
                    if not retried:
                        session.rollback()
                        return False
                    session.commit()
                    return False

    # -------------------------------------------------------------------------
    # Query Razorpay for Link Status
    # -------------------------------------------------------------------------
    discovered_link = None
    try:
        if plink_id:
            discovered_link = gateway.fetch_payment_link(plink_id)
        elif reference_id:
            discovered_link = gateway.fetch_payment_link_by_reference_id(reference_id)
    except GatewayResourceNotFoundError:
        discovered_link = None
    except (GatewayTimeoutError, TimeoutError) as err:
        logger.warning("Reconciliation gateway timeout for ref %s: %s", reference_id, err)
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
        logger.error("Reconciliation gateway error for ref %s: %s", reference_id, err)
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

    # -------------------------------------------------------------------------
    # Apply Authoritative Reconciliation Under Local Transaction
    # -------------------------------------------------------------------------
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
            "lost ownership of reconciliation task %s. Rolling back with zero mutations.",
            worker_id,
            claimed_lease_version,
            task.id,
        )
        session.rollback()
        return False

    opp_orm = session.get(RecoveryOpportunityORM, opp_id) if opp_id else None
    now = utc_now()

    if discovered_link is not None:
        status = discovered_link.status.lower()
        logger.info(
            "Reconciliation discovered link %s with status=%s for ref=%s",
            discovered_link.id,
            status,
            reference_id,
        )

        if status == "paid":
            # Authoritative Payment Evidence
            if voucher_orm:
                voucher_orm.execution_status = ExecutionStatus.EXECUTED.value
                voucher_orm.external_reference_id = discovered_link.id
                voucher_orm.executed_at = now

            if opp_orm and opp_orm.current_state != OpportunityState.RECOVERED.value:
                opp_orm.current_state = OpportunityState.RECOVERED.value
                opp_orm.closed_at = now

        elif status in ("created", "partially_paid"):
            if voucher_orm:
                voucher_orm.execution_status = ExecutionStatus.EXECUTED.value
                voucher_orm.external_reference_id = discovered_link.id
                voucher_orm.executed_at = now

            if opp_orm and opp_orm.current_state in (
                OpportunityState.ACTION_EXECUTING.value,
                OpportunityState.RECONCILIATION_REQUIRED.value,
            ):
                opp_orm.current_state = OpportunityState.AWAITING_SETTLEMENT.value

        else:
            # expired or cancelled
            if voucher_orm:
                voucher_orm.execution_status = ExecutionStatus.CANCELLED_STALE_STATE.value
                voucher_orm.external_reference_id = discovered_link.id

            if opp_orm and opp_orm.current_state in (
                OpportunityState.ACTION_EXECUTING.value,
                OpportunityState.RECONCILIATION_REQUIRED.value,
            ):
                opp_orm.current_state = OpportunityState.OPEN.value

    else:
        # ---------------------------------------------------------------------
        # Guardrail 8: Absence Semantics (Resource Not Found on Gateway)
        # ---------------------------------------------------------------------
        logger.info(
            "Reconciliation: No currently discoverable Payment Link for ref=%s. "
            "Absence is not proof of non-execution. Marking voucher FAILED and "
            "resetting opportunity to OPEN.",
            reference_id,
        )
        if voucher_orm:
            voucher_orm.execution_status = ExecutionStatus.FAILED.value
            voucher_orm.failure_message = "No discoverable Payment Link on gateway"

        if opp_orm and opp_orm.current_state in (
            OpportunityState.ACTION_EXECUTING.value,
            OpportunityState.RECONCILIATION_REQUIRED.value,
        ):
            # Reset to OPEN so it undergoes full economic evaluation on next cycle
            opp_orm.current_state = OpportunityState.OPEN.value

    # Mark current task completed under verified lock
    current_task.status = "COMPLETED"
    current_task.locked_by = None
    current_task.locked_at = None
    session.commit()
    return True
