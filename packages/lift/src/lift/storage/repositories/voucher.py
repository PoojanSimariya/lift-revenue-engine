"""Execution record repository for managing idempotent execution vouchers."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lift.core.errors import IdempotencyConflictError, RecordNotFoundError
from lift.domain.models import ExecutionRecord
from lift.storage.mappers import to_execution_record_domain, to_execution_record_orm
from lift.storage.orm_models import ExecutionRecordORM
from lift.storage.repositories.base import BaseRepository


class ExecutionRecordRepository(BaseRepository):
    """Repository handling execution vouchers and two-phase dispatch state."""

    def get_by_id(self, record_id: UUID) -> ExecutionRecord | None:
        stmt = select(ExecutionRecordORM).where(ExecutionRecordORM.id == record_id)
        orm = self.session.scalar(stmt)
        return to_execution_record_domain(orm) if orm else None

    def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionRecord | None:
        stmt = select(ExecutionRecordORM).where(
            ExecutionRecordORM.idempotency_key == idempotency_key
        )
        orm = self.session.scalar(stmt)
        return to_execution_record_domain(orm) if orm else None

    def get_by_reference_id(self, reference_id: str) -> ExecutionRecord | None:
        stmt = select(ExecutionRecordORM).where(ExecutionRecordORM.reference_id == reference_id)
        orm = self.session.scalar(stmt)
        return to_execution_record_domain(orm) if orm else None

    def get_by_external_reference_id(self, external_reference_id: str) -> ExecutionRecord | None:
        stmt = select(ExecutionRecordORM).where(
            ExecutionRecordORM.external_reference_id == external_reference_id
        )
        orm = self.session.scalar(stmt)
        return to_execution_record_domain(orm) if orm else None


    def create_voucher(self, record: ExecutionRecord) -> ExecutionRecord:
        """Create a new execution voucher.

        Raises:
            IdempotencyConflictError: If idempotency_key or reference_id already exists.
        """
        existing = self.get_by_idempotency_key(record.idempotency_key)
        if existing is not None:
            raise IdempotencyConflictError(record.idempotency_key)

        orm = to_execution_record_orm(record)
        self.session.add(orm)
        try:
            self.session.flush()
        except IntegrityError as err:
            raise IdempotencyConflictError(record.idempotency_key) from err

        return to_execution_record_domain(orm)

    def update(self, record: ExecutionRecord) -> ExecutionRecord:
        """Update voucher execution status and outcomes."""
        stmt = select(ExecutionRecordORM).where(ExecutionRecordORM.id == record.id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("ExecutionRecord", record.id)

        orm.execution_status = record.execution_status.value
        orm.external_reference_id = record.external_reference_id
        orm.failure_message = record.failure_message
        orm.executed_at = record.executed_at
        self.session.flush()
        return to_execution_record_domain(orm)
