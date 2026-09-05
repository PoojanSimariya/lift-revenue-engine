"""Payment attempt repository for persistence operations on payment attempts."""

from __future__ import annotations

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
