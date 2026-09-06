"""Background Lease Recovery and External Reconciliation Reaper.

Separates two distinct maintenance concerns:
  Concern A: Lease Recovery (internal queue health; fences expired workers and requeues tasks)
  Concern B: External Reconciliation Sweep (sweeps stuck ACTION_EXECUTING opportunities;
             verifies dispatch lease is expired before enqueuing reconciliation)
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from lift.gateway.interface import PaymentGatewayAdapter
from lift.storage.base import utc_now
from lift.storage.orm_models import (
    ExecutionRecordORM,
    TaskQueueORM,
)
from lift.storage.repositories.opportunity import OpportunityRepository
from lift.storage.repositories.task import TaskQueueRepository

logger = logging.getLogger(__name__)


class ReaperDaemon:
    """Periodic maintenance daemon for lease recovery and stuck opportunity reconciliation."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: PaymentGatewayAdapter,
        task_lease_timeout_seconds: int = 60,
        opportunity_stuck_timeout_seconds: int = 90,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.task_lease_timeout_seconds = task_lease_timeout_seconds
        self.opportunity_stuck_timeout_seconds = opportunity_stuck_timeout_seconds

    def recover_expired_leases(self) -> int:
        """Concern A: Reclaim expired task leases and fence stale workers.

        Pure database operation; zero external network calls.
        Increments lease_version to fence any frozen/stale worker.
        """
        reclaimed_count = 0
        with self.session_factory() as session:
            task_repo = TaskQueueRepository(session)
            stuck_tasks = task_repo.find_stuck_running_tasks(
                timeout_seconds=self.task_lease_timeout_seconds
            )
            for t in stuck_tasks:
                new_version = task_repo.reclaim_stuck_task(t.id)
                if new_version is not None:
                    logger.warning(
                        "Reclaimed stuck task %s: incremented lease_version to %s",
                        t.id,
                        new_version,
                    )
                    reclaimed_count += 1
            session.commit()
        return reclaimed_count

    def sweep_stuck_opportunities(self) -> int:
        """Concern B: External Reconciliation Sweep.

        Sweeps opportunities in ACTION_EXECUTING for longer than timeout.
        MANDATORY GUARDRAIL 3:
        Verifies that original dispatch task does NOT have an active valid lease
        before taking over / enqueuing reconciliation.
        """
        enqueued_count = 0
        now = utc_now()

        with self.session_factory() as session:
            opp_repo = OpportunityRepository(session)
            task_repo = TaskQueueRepository(session)
            stuck_opps = opp_repo.find_stuck_executing(
                timeout_seconds=self.opportunity_stuck_timeout_seconds
            )

            for opp in stuck_opps:
                from lift.storage.orm_models import RecoveryDecisionORM

                active_voucher = (
                    session.query(ExecutionRecordORM)
                    .join(
                        RecoveryDecisionORM,
                        ExecutionRecordORM.decision_id == RecoveryDecisionORM.id,
                    )
                    .filter(
                        RecoveryDecisionORM.opportunity_id == opp.id,
                        ExecutionRecordORM.execution_status.in_(
                            ["CLAIMED", "RECONCILIATION_REQUIRED"]
                        ),
                    )
                    .order_by(ExecutionRecordORM.claimed_at.desc())
                    .first()
                )

                # Check linked task lease status
                if active_voucher and active_voucher.task_id:
                    dispatch_task = session.get(TaskQueueORM, active_voucher.task_id)
                    if dispatch_task and dispatch_task.status == "RUNNING":
                        # If lease is still active (< task_lease_timeout_seconds), DO NOT TAKE OVER
                        if dispatch_task.locked_at:
                            locked_at = dispatch_task.locked_at
                            if locked_at.tzinfo is None:
                                from datetime import timezone

                                locked_at = locked_at.replace(tzinfo=timezone.utc)
                            if (now - locked_at).total_seconds() < self.task_lease_timeout_seconds:
                                logger.info(
                                    "Sweeper: Skipping opp %s because dispatch task %s "
                                    "has active valid lease",
                                    opp.id,
                                    dispatch_task.id,
                                )
                                continue

                # Original task lease is expired or lost: Enqueue dedicated reconciliation task
                ref_id = active_voucher.reference_id if active_voucher else None
                task_repo.enqueue_task(
                    task_type="RECONCILE_PAYMENT_LINK",
                    payload={
                        "opportunity_id": str(opp.id),
                        "voucher_id": str(active_voucher.id) if active_voucher else None,
                        "reference_id": ref_id,
                    },
                    priority=5,
                )
                enqueued_count += 1
                logger.info(
                    "Sweeper: Enqueued RECONCILE_PAYMENT_LINK task for stuck opp %s", opp.id
                )

            session.commit()
        return enqueued_count

    def run_cycle(self) -> tuple[int, int]:
        """Run one complete reaper maintenance cycle."""
        reclaimed_leases = self.recover_expired_leases()
        reconciled_opps = self.sweep_stuck_opportunities()
        return reclaimed_leases, reconciled_opps
