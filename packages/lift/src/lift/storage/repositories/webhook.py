"""Webhook event repository for deduplication and processing tracking."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lift.core.errors import IdempotencyConflictError, RecordNotFoundError
from lift.storage.base import utc_now
from lift.storage.orm_models import WebhookEventORM
from lift.storage.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository):
    """Repository handling deduplication and lifecycle of incoming webhook events."""

    def get_by_event_id(self, event_id: str) -> WebhookEventORM | None:
        stmt = select(WebhookEventORM).where(WebhookEventORM.event_id == event_id)
        return self.session.scalar(stmt)

    def record_event(
        self,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookEventORM:
        """Record an incoming webhook event.

        Raises:
            IdempotencyConflictError: If event_id has already been recorded.
        """
        existing = self.get_by_event_id(event_id)
        if existing is not None:
            raise IdempotencyConflictError(event_id, "Duplicate webhook event ID")

        orm = WebhookEventORM(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            status="PENDING",
            received_at=utc_now(),
        )
        self.session.add(orm)
        try:
            self.session.flush()
        except IntegrityError as err:
            raise IdempotencyConflictError(event_id, "Duplicate webhook event ID") from err

        return orm

    def mark_processed(self, event_id: str) -> WebhookEventORM:
        """Mark a webhook event as successfully processed."""
        orm = self.get_by_event_id(event_id)
        if not orm:
            raise RecordNotFoundError("WebhookEvent", event_id)

        orm.status = "PROCESSED"
        orm.processed_at = utc_now()
        self.session.flush()
        return orm
