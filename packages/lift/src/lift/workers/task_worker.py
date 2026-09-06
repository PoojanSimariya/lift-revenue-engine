"""Standalone background Task Worker daemon.

Polls task_queue via SELECT ... FOR UPDATE SKIP LOCKED.
Guarantees monotonic lease_version fencing across all state transitions.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from lift.gateway.interface import PaymentGatewayAdapter
from lift.storage.orm_models import TaskQueueORM
from lift.storage.repositories.task import TaskQueueRepository
from lift.workers.handlers import (
    handle_cancel_payment_link,
    handle_evaluate_opportunity,
    handle_reconcile_payment,
    handle_reconcile_payment_link,
)

logger = logging.getLogger(__name__)


class TaskWorker:
    """Core asynchronous task queue worker daemon."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: PaymentGatewayAdapter,
        worker_id: str | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id = (
            worker_id or f"worker_{socket.gethostname()}_{os.getpid()}_{uuid4().hex[:8]}"
        )

        self._handlers: dict[
            str, Callable[[Session, TaskQueueORM, PaymentGatewayAdapter, str, int], bool]
        ] = {
            "EVALUATE_OPPORTUNITY": handle_evaluate_opportunity,
            "CANCEL_PAYMENT_LINK": handle_cancel_payment_link,
            "RECONCILE_PAYMENT_LINK": handle_reconcile_payment_link,
            "RECONCILE_PAYMENT": handle_reconcile_payment,
        }

    def run_once(self) -> bool:
        """Poll and execute at most one task.

        Returns True if a task was processed, False if the queue was empty.
        """
        with self.session_factory() as session:
            task_repo = TaskQueueRepository(session)
            claim = task_repo.claim_next_task(self.worker_id)
            if not claim:
                session.rollback()
                return False

            task, claimed_lease_version = claim
            session.commit()

            task_id = task.id
            task_type = task.task_type

        # Dispatch task to registered handler in a fresh session
        handler = self._handlers.get(task_type)
        if not handler:
            logger.error("No handler registered for task_type: %s", task_type)
            with self.session_factory() as session:
                task_repo = TaskQueueRepository(session)
                task_repo.fail_task_permanently(
                    task_id,
                    claimed_lease_version,
                    self.worker_id,
                    f"Unknown task_type: {task_type}",
                )
                session.commit()
            return True

        with self.session_factory() as session:
            # Re-fetch task ORM for the handler
            task_orm = session.get(TaskQueueORM, task_id)
            if not task_orm:
                return False

            try:
                handler(session, task_orm, self.gateway, self.worker_id, claimed_lease_version)
            except Exception as err:
                logger.error("Unhandled error executing task %s (%s): %s", task_id, task_type, err)
                session.rollback()
                with self.session_factory() as retry_session:
                    from datetime import timedelta

                    from lift.storage.base import utc_now

                    repo = TaskQueueRepository(retry_session)
                    repo.retry_task(
                        task_id,
                        claimed_lease_version,
                        self.worker_id,
                        str(err),
                        utc_now() + timedelta(seconds=15),
                    )
                    retry_session.commit()

        return True

    def run(
        self,
        stop_event: Any | None = None,
        max_iterations: int | None = None,
    ) -> int:
        """Continuous execution loop."""
        logger.info("Starting TaskWorker daemon [%s]", self.worker_id)
        processed_count = 0
        iterations = 0

        while True:
            if stop_event and stop_event.is_set():
                logger.info("TaskWorker [%s] stopping on stop_event", self.worker_id)
                break

            if max_iterations is not None and iterations >= max_iterations:
                break

            iterations += 1
            processed = self.run_once()
            if processed:
                processed_count += 1
            else:
                time.sleep(self.poll_interval_seconds)

        return processed_count
