"""Payment attempt repository for persistence operations on payment attempts."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from lift.core.errors import RecordNotFoundError
from lift.domain.models import PaymentAttempt
from lift.storage.mappers import to_attempt_domain, to_attempt_orm
from lift.storage.orm_models import PaymentAttemptORM
from lift.storage.repositories.base import BaseRepository


class PaymentAttemptRepository(BaseRepository):
    """Repository handling payment attempt ingestion, lookup, and opportunity association."""

    def get_by_id(self, attempt_id: UUID) -> PaymentAttempt | None:
        stmt = select(PaymentAttemptORM).where(PaymentAttemptORM.id == attempt_id)
        orm = self.session.scalar(stmt)
        return to_attempt_domain(orm) if orm else None

    def get_by_payment_id(self, razorpay_payment_id: str) -> PaymentAttempt | None:
        stmt = select(PaymentAttemptORM).where(
            PaymentAttemptORM.razorpay_payment_id == razorpay_payment_id
        )
        orm = self.session.scalar(stmt)
        return to_attempt_domain(orm) if orm else None

    def list_by_opportunity_id(self, opportunity_id: UUID) -> list[PaymentAttempt]:
        stmt = (
            select(PaymentAttemptORM)
            .where(PaymentAttemptORM.recovery_opportunity_id == opportunity_id)
            .order_by(PaymentAttemptORM.attempt_sequence.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_attempt_domain(orm) for orm in results]

    def create(self, attempt: PaymentAttempt) -> PaymentAttempt:
        orm = to_attempt_orm(attempt)
        self.session.add(orm)
        self.session.flush()
        return to_attempt_domain(orm)

    def update(self, attempt: PaymentAttempt) -> PaymentAttempt:
        stmt = select(PaymentAttemptORM).where(PaymentAttemptORM.id == attempt.id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("PaymentAttempt", attempt.id)

        orm.recovery_opportunity_id = attempt.recovery_opportunity_id
        orm.status = attempt.status.value
        orm.error_code = attempt.error_code
        orm.error_description = attempt.error_description
        orm.error_source = attempt.error_source
        orm.error_step = attempt.error_step
        orm.error_reason = attempt.error_reason
        orm.raw_payload = attempt.raw_payload
        self.session.flush()
        return to_attempt_domain(orm)

    def update_status_monotonic(
        self,
        attempt_id: UUID,
        new_status: str,
        error_code: str | None = None,
        error_description: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> PaymentAttempt:
        """Update payment attempt status monotonically.

        Monotonic hierarchy: failed -> authorized -> captured.
        Status never regresses (e.g. captured never reverts to authorized or failed).
        """
        stmt = select(PaymentAttemptORM).where(PaymentAttemptORM.id == attempt_id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("PaymentAttempt", attempt_id)

        rank_map = {"failed": 1, "authorized": 2, "captured": 3}
        current_rank = rank_map.get(orm.status.lower(), 0)
        new_rank = rank_map.get(new_status.lower(), 0)

        # Monotonic advance only
        if new_rank > current_rank:
            orm.status = new_status.lower()

        if error_code is not None:
            orm.error_code = error_code
        if error_description is not None:
            orm.error_description = error_description
        if raw_payload is not None:
            orm.raw_payload = raw_payload

        self.session.flush()
        return to_attempt_domain(orm)
