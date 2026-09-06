"""Task handler for EVALUATE_OPPORTUNITY tasks."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from lift.core.errors import StaleWorkerFencedError
from lift.gateway.interface import PaymentGatewayAdapter
from lift.services.execution import ExecutionSafetyService
from lift.storage.orm_models import TaskQueueORM
from lift.storage.repositories.task import TaskQueueRepository

logger = logging.getLogger(__name__)


def handle_evaluate_opportunity(
    session: Session,
    task: TaskQueueORM,
    gateway: PaymentGatewayAdapter,
    worker_id: str,
    claimed_lease_version: int,
) -> bool:
    """Execute EVALUATE_OPPORTUNITY task using the 3-phase execution safety coordinator."""
    payload = task.payload
    raw_opp_id = payload.get("opportunity_id")
    if not raw_opp_id:
        logger.error("Task %s missing opportunity_id in payload", task.id)
        task_repo = TaskQueueRepository(session)
        task_repo.fail_task_permanently(
            task.id, claimed_lease_version, worker_id, "Missing opportunity_id"
        )
        session.commit()
        return False

    opportunity_id = UUID(str(raw_opp_id))
    safety_service = ExecutionSafetyService(session=session, gateway=gateway)
    task_repo = TaskQueueRepository(session)

    # -------------------------------------------------------------------------
    # Phase 1: Lock & Evaluate Intent (in transaction)
    # -------------------------------------------------------------------------
    try:
        phase_1_result = safety_service.execute_phase_1(
            opportunity_id=opportunity_id,
            task_id=task.id,
            claimed_lease_version=claimed_lease_version,
            worker_id=worker_id,
        )
        session.commit()
    except Exception as err:
        session.rollback()
        logger.error("Phase 1 failed for task %s, opp %s: %s", task.id, opportunity_id, err)
        # Reschedule retry if possible
        from datetime import timedelta

        from lift.storage.base import utc_now

        task_repo.retry_task(
            task.id,
            claimed_lease_version,
            worker_id,
            str(err),
            utc_now() + timedelta(seconds=10),
        )
        session.commit()
        return False

    if not phase_1_result.should_dispatch:
        # No external dispatch required (blocked, already recovered, terminal, or internal retry)
        logger.info(
            "Task %s Phase 1 completed without dispatch: %s",
            task.id,
            phase_1_result.skip_reason,
        )
        task_repo.complete_task(task.id, claimed_lease_version, worker_id)
        session.commit()
        return True

    # -------------------------------------------------------------------------
    # Phase 2: Out-of-Transaction External Dispatch (NO DB LOCKS HELD)
    # -------------------------------------------------------------------------
    phase_2_result = safety_service.execute_phase_2(phase_1_result)

    # -------------------------------------------------------------------------
    # Phase 3: Fenced Settlement & Task Completion (in new transaction)
    # -------------------------------------------------------------------------
    assert phase_1_result.voucher_id is not None
    try:
        safety_service.execute_phase_3(
            task_id=task.id,
            claimed_lease_version=claimed_lease_version,
            worker_id=worker_id,
            voucher_id=phase_1_result.voucher_id,
            opportunity_id=opportunity_id,
            phase_2_result=phase_2_result,
        )
        session.commit()
        return True
    except StaleWorkerFencedError:
        logger.warning(
            "Worker %s was fenced during Phase 3 for task %s (lease=%s). Rolling back.",
            worker_id,
            task.id,
            claimed_lease_version,
        )
        session.rollback()
        return False
    except Exception as err:
        session.rollback()
        logger.error("Phase 3 settlement error for task %s: %s", task.id, err)
        return False
