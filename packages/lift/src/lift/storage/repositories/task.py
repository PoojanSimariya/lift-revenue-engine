"""Task queue repository for durable asynchronous task enqueueing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from lift.storage.base import utc_now
from lift.storage.orm_models import TaskQueueORM
from lift.storage.repositories.base import BaseRepository


class TaskQueueRepository(BaseRepository):
    """Repository handling persistence of asynchronous processing tasks."""

    def get_by_id(self, task_id: uuid.UUID) -> TaskQueueORM | None:
        """Fetch a task by primary key."""
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        return self.session.scalar(stmt)

    def enqueue_task(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: int = 10,
        scheduled_at: datetime | None = None,
    ) -> TaskQueueORM:
        """Persist a task into task_queue within the caller's active database transaction."""
        orm = TaskQueueORM(
            id=uuid.uuid4(),
            task_type=task_type,
            payload=payload,
            status="QUEUED",
            priority=priority,
            scheduled_at=scheduled_at or utc_now(),
            attempts=0,
            max_attempts=3,
            created_at=utc_now(),
        )
        self.session.add(orm)
        self.session.flush()
        return orm

    def claim_next_task(self, worker_id: str) -> tuple[TaskQueueORM, int] | None:
        """Poll and atomically claim the next eligible queued task.

        Increments lease_version by 1 and sets locked_by and locked_at.
        Uses SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL.
        """
        now = utc_now()
        stmt = (
            select(TaskQueueORM)
            .where(TaskQueueORM.status == "QUEUED", TaskQueueORM.scheduled_at <= now)
            .order_by(TaskQueueORM.priority.asc(), TaskQueueORM.scheduled_at.asc())
            .limit(1)
        )

        bind = self.session.get_bind()
        if bind and bind.dialect.name == "sqlite":
            stmt = stmt.with_for_update()
        else:
            stmt = stmt.with_for_update(skip_locked=True)

        task = self.session.scalar(stmt)
        if not task:
            return None

        task.lease_version += 1
        task.status = "RUNNING"
        task.locked_by = worker_id
        task.locked_at = now
        self.session.flush()
        return task, task.lease_version

    def complete_task(self, task_id: uuid.UUID, lease_version: int, worker_id: str) -> bool:
        """Fenced completion of a task.

        Returns True if the task was completed under the active lease,
        or False if fenced (ownership lost).
        """
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        bind = self.session.get_bind()
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task = self.session.scalar(stmt)

        if (
            not task
            or task.lease_version != lease_version
            or task.locked_by != worker_id
            or task.status != "RUNNING"
        ):
            return False

        task.status = "COMPLETED"
        task.locked_by = None
        task.locked_at = None
        self.session.flush()
        return True

    def retry_task(
        self,
        task_id: uuid.UUID,
        lease_version: int,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        """Fenced retry of a task with backoff.

        Returns True if rescheduled, or False if fenced.
        """
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        bind = self.session.get_bind()
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task = self.session.scalar(stmt)

        if (
            not task
            or task.lease_version != lease_version
            or task.locked_by != worker_id
            or task.status != "RUNNING"
        ):
            return False

        task.attempts += 1
        task.last_error = error
        task.locked_by = None
        task.locked_at = None
        if task.attempts >= task.max_attempts:
            task.status = "FAILED"
        else:
            task.status = "QUEUED"
            task.scheduled_at = next_attempt_at

        self.session.flush()
        return True

    def fail_task_permanently(
        self,
        task_id: uuid.UUID,
        lease_version: int,
        worker_id: str,
        error: str,
    ) -> bool:
        """Fenced permanent failure of a task."""
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        bind = self.session.get_bind()
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task = self.session.scalar(stmt)

        if (
            not task
            or task.lease_version != lease_version
            or task.locked_by != worker_id
            or task.status != "RUNNING"
        ):
            return False

        task.attempts += 1
        task.last_error = error
        task.status = "FAILED"
        task.locked_by = None
        task.locked_at = None
        self.session.flush()
        return True

    def renew_lease(self, task_id: uuid.UUID, lease_version: int, worker_id: str) -> bool:
        """Fenced lease renewal for a running task."""
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        bind = self.session.get_bind()
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task = self.session.scalar(stmt)

        if (
            not task
            or task.lease_version != lease_version
            or task.locked_by != worker_id
            or task.status != "RUNNING"
        ):
            return False

        task.locked_at = utc_now()
        self.session.flush()
        return True

    def find_stuck_running_tasks(self, timeout_seconds: int = 60) -> list[TaskQueueORM]:
        """Find tasks stuck in RUNNING whose lease has expired."""
        from datetime import timedelta

        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        stmt = (
            select(TaskQueueORM)
            .where(
                TaskQueueORM.status == "RUNNING",
                TaskQueueORM.locked_at <= cutoff,
            )
            .order_by(TaskQueueORM.locked_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def reclaim_stuck_task(self, task_id: uuid.UUID) -> int | None:
        """Atomically reclaim a stuck running task and increment its lease_version."""
        stmt = select(TaskQueueORM).where(TaskQueueORM.id == task_id)
        bind = self.session.get_bind()
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        task = self.session.scalar(stmt)

        if not task or task.status != "RUNNING":
            return None

        task.lease_version += 1
        task.status = "QUEUED"
        task.locked_by = None
        task.locked_at = None
        self.session.flush()
        return task.lease_version
