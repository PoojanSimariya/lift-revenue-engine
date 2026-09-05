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
